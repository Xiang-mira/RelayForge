import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from relayforge.journal.state import (
    Conflict,
    EffectClass,
    JournalError,
    Status,
    idempotency_key,
    payload_hash,
    summary,
    validate_transition,
)
from relayforge.models import Effect, RecoveryDecision, Run, RunEvent


class Journal:
    def __init__(self, sessions):
        self.sessions = sessions

    @staticmethod
    def event(session, effect: Effect, event_type: str, data: dict):
        session.add(
            RunEvent(run_id=effect.run_id, effect_id=effect.effect_id, type=event_type, data=data)
        )

    def prepare(
        self,
        run_id: str,
        branch_id: str,
        action_id: str,
        tool: str,
        classification: EffectClass,
        payload: dict,
        policy: str,
    ) -> Effect:
        digest = payload_hash(payload)
        conflict_id = None
        # Unique constraint arbitrates the initial insert race; retry the read after rollback.
        for retry in range(2):
            try:
                with self.sessions.begin() as session:
                    run = session.get(Run, run_id)
                    if not run or run.branch_id != branch_id:
                        raise JournalError("Run does not own this branch")
                    effect = session.scalar(
                        select(Effect)
                        .where(Effect.branch_id == branch_id, Effect.logical_action_id == action_id)
                        .with_for_update()
                    )
                    if effect:
                        if (effect.payload_hash, effect.tool_name, effect.effect_class) != (
                            digest,
                            tool,
                            classification,
                        ):
                            conflict_id = effect.effect_id
                            self.event(
                                session,
                                effect,
                                "effect.conflict",
                                {
                                    "reason": "identity_input_mismatch",
                                    "recorded_hash": effect.payload_hash,
                                    "requested_hash": digest,
                                    "tool_changed": effect.tool_name != tool,
                                    "classification_changed": effect.effect_class != classification,
                                },
                            )
                        else:
                            session.expunge(effect)
                            return effect
                    else:
                        effect = Effect(
                            run_id=run_id,
                            branch_id=branch_id,
                            logical_action_id=action_id,
                            tool_name=tool,
                            effect_class=classification,
                            payload_hash=digest,
                            payload_summary=summary(payload),
                            idempotency_key=idempotency_key(branch_id, action_id),
                            policy_decision=policy,
                        )
                        session.add(effect)
                        session.flush()
                        self.event(session, effect, "effect.prepared", {"status": effect.status})
                        session.expunge(effect)
                        return effect
                if conflict_id:
                    raise Conflict("Logical action parameters conflict with recorded intent")
            except IntegrityError:
                if retry:
                    raise
        raise JournalError("Unable to prepare intent")

    def get(self, effect_id: str) -> Effect:
        with self.sessions() as session:
            effect = session.get(Effect, effect_id)
            if not effect:
                raise JournalError("Effect not found")
            session.expunge(effect)
            return effect

    def dispatch(self, effect_id: str, authorized: bool) -> bool:
        with self.sessions.begin() as session:
            effect = session.get(Effect, effect_id, with_for_update=True)
            if not effect:
                raise JournalError("Effect not found")
            run = session.get(Run, effect.run_id, with_for_update=True)
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                self.event(
                    session, effect, "effect.cancelled_before_dispatch", {"status": run.status}
                )
                return False
            if not authorized:
                self.event(session, effect, "effect.denied", {"reason": "authorization_required"})
                return False
            if effect.status != Status.PREPARED:
                self.event(session, effect, "effect.dispatch_suppressed", {"status": effect.status})
                return False
            validate_transition(effect.status, Status.IN_FLIGHT)
            effect.status = Status.IN_FLIGHT
            effect.attempt_count += 1
            self.event(
                session,
                effect,
                "effect.dispatched",
                {"attempt": effect.attempt_count, "monotonic_ns": time.monotonic_ns()},
            )
            return True

    def native_dispatch(self, effect_id: str, authorized: bool, guaranteed: bool) -> bool:
        # A native-key retry never asserts absence and keeps UNKNOWN until a receipt.
        with self.sessions.begin() as session:
            effect = session.get(Effect, effect_id, with_for_update=True)
            if not effect:
                raise JournalError("Effect not found")
            run = session.get(Run, effect.run_id, with_for_update=True)
            if (
                not authorized
                or not guaranteed
                or run.status in {"CANCELLED", "CANCEL_REQUESTED"}
                or effect.effect_class != EffectClass.IDEMPOTENT_WRITE
                or effect.status != Status.UNKNOWN
                or effect.reconciliation_method != "native_idempotency_available"
            ):
                return False
            effect.reconciliation_method = "native_retransmission_in_flight"
            effect.reconciliation_result = {
                "outcome": "indeterminate",
                "method": "native_retransmission_in_flight",
            }
            effect.attempt_count += 1
            self.event(
                session,
                effect,
                "effect.native_retransmitted",
                {
                    "attempt": effect.attempt_count,
                    "monotonic_ns": time.monotonic_ns(),
                    "guarantee": "same_native_idempotency_key",
                    "absence_not_assumed": True,
                },
            )
            return True

    def outcome(
        self,
        effect_id: str,
        target: Status,
        evidence: str | None,
        receipt: dict | None = None,
        method: str = "tool_response",
    ) -> Effect:
        with self.sessions.begin() as session:
            effect = session.get(Effect, effect_id, with_for_update=True)
            if not effect:
                raise JournalError("Effect not found")
            validate_transition(effect.status, target, evidence)
            if target == Status.CONFIRMED and (
                not receipt
                or not isinstance(receipt.get("external_id"), str)
                or not 1 <= len(receipt["external_id"]) <= 200
                or receipt.get("provider") not in {"mock", "local_mock", "managed_workspace"}
                or set(receipt) != {"external_id", "provider"}
            ):
                raise JournalError(
                    "Confirmed outcome requires a recognized opaque provider receipt"
                )
            old = effect.status
            effect.status = target
            safe_evidence = {"outcome": evidence or "indeterminate", "method": method}
            if receipt:
                # Receipts contain opaque identifiers, never arbitrary tool result text.
                effect.external_receipt = {
                    k: str(receipt[k]) for k in ("external_id", "provider") if k in receipt
                }
            effect.reconciliation_method = method
            effect.reconciliation_result = safe_evidence
            session.add(
                RecoveryDecision(effect_id=effect_id, evidence=safe_evidence, decision=str(target))
            )
            self.event(
                session,
                effect,
                "effect.outcome",
                {
                    "from": old,
                    "status": target,
                    "monotonic_ns": time.monotonic_ns(),
                    **safe_evidence,
                },
            )
            session.flush()
            session.expunge(effect)
            return effect

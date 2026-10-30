from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from relayforge.journal.service import Journal
from relayforge.journal.state import (
    Conflict,
    EffectClass,
    JournalError,
    RunCancelled,
    Status,
    payload_hash,
)
from relayforge.models import Branch, Effect, EffectReference, Run, RunEvent
from relayforge.policies.service import Approvals
from relayforge.providers.mock_external import InjectedCrash, KnownNoEffect, MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.publication import PublicationGate, PublicationRejected


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    subject: str = Field(min_length=1, max_length=200)


class PermissionDenied(JournalError):
    pass


class ApprovalRequired(JournalError):
    pass


class OutcomeUnknown(JournalError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    classification: EffectClass
    policy: str


TOOLS = {
    "mock.create": ToolSpec(EffectClass.IDEMPOTENT_WRITE, "allow"),
    "mock.notify": ToolSpec(EffectClass.NON_IDEMPOTENT_WRITE, "ask"),
    "real.external_write": ToolSpec(EffectClass.NON_IDEMPOTENT_WRITE, "deny"),
    "workspace.read": ToolSpec(EffectClass.READ_ONLY, "allow"),
    "workspace.diff": ToolSpec(EffectClass.READ_ONLY, "allow"),
    "workspace.write": ToolSpec(EffectClass.WORKSPACE_MUTATION, "allow"),
    "workspace.test": ToolSpec(EffectClass.WORKSPACE_MUTATION, "allow"),
}


class ToolExecutor:
    def __init__(
        self, journal: Journal, external: MockExternal, workspace: WorkspaceTools | None = None
    ):
        self.journal = journal
        self.external = external
        self.approvals = Approvals(journal.sessions)
        self.workspace = workspace
        self.publications = PublicationGate(journal.sessions, workspace)

    def execute(
        self,
        run_id: str,
        branch_id: str,
        action_id: str,
        tool_name: str,
        payload: dict,
        fault: str = "none",
    ) -> dict:
        spec = TOOLS.get(tool_name)
        if not spec:
            raise PermissionDenied("Tool is not registered")
        is_workspace = tool_name.startswith("workspace.")
        if is_workspace:
            if not self.workspace:
                raise PermissionDenied("Managed workspace tools unavailable")
            self.workspace.validate(tool_name, branch_id, payload)
        else:
            ToolInput.model_validate(payload)
            if spec.policy != "deny" and self.publications.required(run_id, action_id):
                proof = self.publications.require(run_id, branch_id)
                payload = {**payload, "_tested_version": proof["version"]}
        policy = spec.policy
        reference_error = None
        with self.journal.sessions.begin() as session:
            run = session.get(Run, run_id)
            branch = session.get(Branch, branch_id)
            if not run or not branch or run.branch_id != branch_id or run.task_id != branch.task_id:
                raise JournalError("Run does not own this branch")
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                raise RunCancelled("Run cancellation prevents further tool execution")
            external_write = spec.classification in {
                EffectClass.IDEMPOTENT_WRITE,
                EffectClass.NON_IDEMPOTENT_WRITE,
                EffectClass.UNKNOWN_OUTCOME,
            }
            if external_write and branch.parent_id and policy != "deny":
                historical = session.scalars(
                    select(Effect)
                    .join(EffectReference, EffectReference.effect_id == Effect.effect_id)
                    .where(
                        EffectReference.branch_id == branch_id,
                        Effect.logical_action_id == action_id,
                    )
                ).all()
                if historical:
                    ancestor = historical[0]
                    if len(historical) != 1 or (ancestor.payload_hash, ancestor.tool_name) != (
                        payload_hash(payload),
                        tool_name,
                    ):
                        reference_error = "conflict"
                    elif ancestor.status != Status.CONFIRMED:
                        reference_error = "unresolved"
                    session.add(
                        RunEvent(
                            run_id=run_id,
                            effect_id=ancestor.effect_id,
                            type="fork.reference_blocked"
                            if reference_error
                            else "fork.effect_reused",
                            data={
                                "parent_effect_id": ancestor.effect_id,
                                "readonly": True,
                                "reason": reference_error,
                            },
                        )
                    )
                    if not reference_error:
                        return ancestor.external_receipt
                if policy != "deny":
                    policy = "ask"
        if reference_error == "conflict":
            raise Conflict(
                "Fork external action conflicts with referenced history; use a new explicit identity"
            )
        if reference_error:
            raise OutcomeUnknown("Referenced parent operation is unresolved; no repeat dispatch")
        if (
            spec.classification == EffectClass.IDEMPOTENT_WRITE
            and not self.external.supports_idempotency
        ):
            raise PermissionDenied("Provider does not promise idempotency for this tool")
        effect = self.journal.prepare(
            run_id, branch_id, action_id, tool_name, spec.classification, payload, policy
        )
        if policy == "deny":
            self.journal.dispatch(effect.effect_id, False)
            raise PermissionDenied("External writes are disabled")
        if effect.status == Status.CONFIRMED:
            return effect.external_receipt
        if self.can_resume_native(effect):
            return self.retransmit_native(effect, payload, fault)
        if effect.status in (Status.IN_FLIGHT, Status.UNKNOWN):
            raise OutcomeUnknown("No repeat dispatch: reconcile after the owner stops")
        if effect.status == Status.FAILED:
            raise JournalError("Explicit safe retry decision required")
        if policy == "ask":
            self.approvals.pending(effect.effect_id)
            effect = self.journal.get(effect.effect_id)
            if not self.approvals.authorized(effect):
                self.journal.dispatch(effect.effect_id, False)
                raise ApprovalRequired("Branch and payload approval required")
        if fault == "before_dispatch":
            raise InjectedCrash("Crash before dispatch claim")
        if not self.journal.dispatch(effect.effect_id, self.approvals.authorized(effect)):
            with self.journal.sessions() as session:
                if session.get(Run, run_id).status in {"CANCELLED", "CANCEL_REQUESTED"}:
                    raise RunCancelled("Run cancelled before tool dispatch")
            raise OutcomeUnknown("Dispatch claim already owned or permission missing")
        if fault == "after_dispatch_before_call":
            raise InjectedCrash("Crash after claim before external call")
        try:
            if is_workspace:
                receipt = self.workspace.execute(
                    tool_name,
                    branch_id,
                    payload,
                    effect.idempotency_key,
                    fault,
                    context={"run_id": run_id, "task_id": run.task_id},
                )
            else:
                receipt = self.call_external(effect, payload, fault)
        except PublicationRejected:
            self.journal.outcome(
                effect.effect_id,
                Status.FAILED,
                "not_occurred",
                method="publication_gate_prevented_dispatch",
            )
            raise
        except KnownNoEffect:
            self.journal.outcome(
                effect.effect_id, Status.FAILED, "not_occurred", method="provider_rejection"
            )
            raise
        except Exception as exc:
            self.journal.outcome(
                effect.effect_id, Status.UNKNOWN, None, method="missing_tool_receipt"
            )
            raise OutcomeUnknown("Tool outcome uncertain; no automatic retry") from exc
        return self.journal.outcome(
            effect.effect_id, Status.CONFIRMED, "occurred", receipt
        ).external_receipt

    def call_external(self, effect, payload, fault):
        if self.publications.required(effect.run_id, effect.logical_action_id):
            # Recheck at the provider boundary, after permission/dispatch claim.
            claimed = self.journal.get(effect.effect_id)
            if (
                claimed.status != Status.IN_FLIGHT
                or not self.approvals.authorized(claimed)
                or claimed.payload_hash != payload_hash(payload)
            ):
                self.publications.reject(effect.run_id, "publication_dispatch_not_authorized")
            self.publications.authorize(effect.run_id, effect.branch_id, payload, effect.effect_id)
        return self.external.execute(
            effect.idempotency_key,
            payload_hash(payload),
            effect.idempotency_key if effect.effect_class == EffectClass.IDEMPOTENT_WRITE else None,
            fault,
        )

    def can_resume_native(self, effect):
        return (
            effect.status == Status.UNKNOWN
            and effect.effect_class == EffectClass.IDEMPOTENT_WRITE
            and effect.tool_name == "mock.create"
            and self.external.supports_idempotency
            and effect.reconciliation_method == "native_idempotency_available"
        )

    def retransmit_native(self, effect, payload, fault):
        if not self.journal.native_dispatch(
            effect.effect_id, self.approvals.authorized(effect), self.external.supports_idempotency
        ):
            with self.journal.sessions() as session:
                if session.get(Run, effect.run_id).status in {"CANCELLED", "CANCEL_REQUESTED"}:
                    raise RunCancelled("Cancellation prevents native retransmission")
            raise OutcomeUnknown("A native retransmission already owns this uncertain action")
        try:
            receipt = self.call_external(effect, payload, fault)
        except PublicationRejected:
            # A denied retransmission cannot prove the earlier uncertain attempt absent.
            raise
        except KnownNoEffect:
            # Rejection of this request says nothing about the earlier uncertain request.
            self.journal.outcome(
                effect.effect_id,
                Status.UNKNOWN,
                None,
                method="native_retry_rejected_original_still_unknown",
            )
            raise OutcomeUnknown(
                "Original outcome remains uncertain after retry rejection"
            ) from None
        except Exception as exc:
            self.journal.outcome(
                effect.effect_id, Status.UNKNOWN, None, method="missing_native_retry_receipt"
            )
            raise OutcomeUnknown("Native retry receipt missing; preserve UNKNOWN") from exc
        return self.journal.outcome(
            effect.effect_id,
            Status.CONFIRMED,
            "occurred",
            receipt,
            method="native_idempotent_response",
        ).external_receipt

    def reconcile(self, effect_id: str, *, owner_stopped: bool = False):
        effect = self.journal.get(effect_id)
        if effect.status == Status.CONFIRMED:
            return effect
        if effect.status not in (Status.IN_FLIGHT, Status.UNKNOWN):
            raise JournalError("Effect has no uncertain outcome")
        if effect.reconciliation_method == "native_retransmission_in_flight" and not owner_stopped:
            raise OutcomeUnknown("Native retransmission is active; ownership cannot be transferred")
        if effect.status == Status.IN_FLIGHT:
            if not owner_stopped:
                raise OutcomeUnknown("Cannot take over an active dispatch")
            effect = self.journal.outcome(effect_id, Status.UNKNOWN, None, method="stopped_owner")
        provider = self.workspace if effect.tool_name.startswith("workspace.") else self.external
        if provider is None:
            raise OutcomeUnknown("Provider unavailable; preserve uncertain outcome")
        try:
            evidence = provider.lookup(
                effect.idempotency_key, effect.payload_hash, quiescent=owner_stopped
            )
        except Exception:
            # Query failure says nothing about the original write. Persist a pause,
            # not absence or native-retry authority; never store provider error text.
            return self.journal.outcome(
                effect_id, Status.UNKNOWN, None, method="provider_query_unavailable"
            )
        if evidence.outcome == "occurred":
            return self.journal.outcome(
                effect_id,
                Status.CONFIRMED,
                "occurred",
                evidence.receipt,
                method="provider_query",
            )
        if evidence.outcome == "not_occurred" and owner_stopped:
            return self.journal.outcome(
                effect_id, Status.FAILED, "not_occurred", method="quiescent_provider_absence"
            )
        native = (
            effect.effect_class == EffectClass.IDEMPOTENT_WRITE
            and effect.tool_name == "mock.create"
            and self.external.supports_idempotency
        )
        return self.journal.outcome(
            effect_id,
            Status.UNKNOWN,
            None,
            method="native_idempotency_available" if native else "insufficient_external_evidence",
        )

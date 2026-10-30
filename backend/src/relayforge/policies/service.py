from sqlalchemy import select

from relayforge.journal.state import JournalError
from relayforge.models import Approval, Effect, Run, RunEvent, now


class Approvals:
    def __init__(self, sessions):
        self.sessions = sessions

    def pending(self, effect_id: str) -> str:
        with self.sessions.begin() as session:
            effect = session.get(Effect, effect_id, with_for_update=True)
            if not effect or effect.policy_decision != "ask":
                raise JournalError("Effect does not support approval")
            if effect.approval_id:
                return effect.approval_id
            approval = Approval(
                effect_id=effect_id, branch_id=effect.branch_id, payload_hash=effect.payload_hash
            )
            session.add(approval)
            session.flush()
            effect.approval_id = approval.id
            session.add(
                RunEvent(
                    run_id=effect.run_id,
                    effect_id=effect_id,
                    type="approval.requested",
                    data={"approval_id": approval.id},
                )
            )
            return approval.id

    def decide(self, effect_id: str, decision: str) -> None:
        if decision not in ("APPROVED", "REJECTED"):
            raise JournalError("Invalid approval decision")
        with self.sessions.begin() as session:
            effect = session.get(Effect, effect_id, with_for_update=True)
            if not effect or not effect.approval_id or effect.status != "PREPARED":
                raise JournalError("No pending approval for this prepared effect")
            if session.get(Run, effect.run_id, with_for_update=True).status in {
                "CANCELLED",
                "CANCEL_REQUESTED",
            }:
                raise JournalError("Cancelled runs cannot receive execution approval")
            approval = session.get(Approval, effect.approval_id, with_for_update=True)
            if not approval or approval.decision != "PENDING":
                raise JournalError("Approval is final or missing")
            if (approval.branch_id, approval.payload_hash) != (
                effect.branch_id,
                effect.payload_hash,
            ):
                raise JournalError("Approval binding mismatch")
            approval.decision = decision
            approval.decided_at = now()
            session.add(
                RunEvent(
                    run_id=effect.run_id,
                    effect_id=effect_id,
                    type="approval.decided",
                    data={"decision": decision},
                )
            )

    def authorized(self, effect: Effect) -> bool:
        if effect.policy_decision == "allow":
            return True
        if effect.policy_decision != "ask" or not effect.approval_id:
            return False
        with self.sessions() as session:
            approval = session.scalar(select(Approval).where(Approval.id == effect.approval_id))
            return bool(
                approval
                and approval.decision == "APPROVED"
                and approval.branch_id == effect.branch_id
                and approval.payload_hash == effect.payload_hash
                and approval.effect_id == effect.effect_id
            )

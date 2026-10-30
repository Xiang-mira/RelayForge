"""Fail-closed acceptance at the managed coding task's external write boundary."""

from sqlalchemy import or_, select

from relayforge.journal.state import (
    JournalError,
    RunCancelled,
    Status,
    idempotency_key,
    logical_action_id,
    payload_hash,
)
from relayforge.models import Effect, EffectReference, Run, RunEvent, Task
from relayforge.providers.templates import TEMPLATES
from relayforge.runtime.catalog import CONTROLLED_TEMPLATES
from relayforge.workspaces.fingerprint import fingerprint, manifest


class PublicationRejected(JournalError):
    pass


class PublicationGate:
    def __init__(self, sessions, workspace):
        self.sessions, self.workspace = sessions, workspace

    def required(self, run_id: str, action_id: str) -> bool:
        with self.sessions() as session:
            run = session.get(Run, run_id)
            task = session.get(Task, run.task_id) if run else None
            # All external tools of API-created coding tasks are protected, including
            # alternative action IDs. Generic protocol fixtures are not publications.
            return action_id == logical_action_id("publish") or bool(
                task and (task.template in TEMPLATES or task.template in CONTROLLED_TEMPLATES)
            )

    def reject(self, run_id: str, reason: str):
        with self.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if run and run.status not in {"CANCELLED", "CANCEL_REQUESTED"}:
                run.status = "NEEDS_REVIEW"
                run.lease_until = None
                session.add(
                    RunEvent(
                        run_id=run_id,
                        type="publication.rejected",
                        data={"reason": reason, "requires_revalidation": True},
                    )
                )
        raise PublicationRejected(reason)

    def require(self, run_id: str, branch_id: str, *, own_dispatch: str | None = None) -> dict:
        try:
            return self._require(run_id, branch_id, own_dispatch=own_dispatch)
        except (OSError, ValueError, KeyError, TypeError):
            self.reject(run_id, "acceptance_evidence_invalid_or_missing")

    def _require(self, run_id: str, branch_id: str, *, own_dispatch: str | None) -> dict:
        if self.workspace is None:
            self.reject(run_id, "managed_workspace_required")
        with self.sessions() as session:
            run = session.get(Run, run_id)
            if not run or run.branch_id != branch_id:
                raise JournalError("Run does not own publication branch")
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                raise RunCancelled("Cancellation prevents publication")
            receipt = {
                "provider": "managed_workspace",
                "external_id": idempotency_key(branch_id, logical_action_id("test")),
            }
            origin_id = run_id
            if run.execution_config.get("controller") == "mcp":
                receipt = run.execution_config.get("test_receipt")
                if not receipt:
                    self.reject(run_id, "test_evidence_missing_retest_required")
            elif not self.workspace._path(receipt["external_id"]).exists():
                fork = session.scalar(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.type == "fork.created")
                    .order_by(RunEvent.id.desc())
                )
                if not fork or not fork.data.get("test_receipt"):
                    self.reject(run_id, "test_evidence_missing_retest_required")
                receipt = fork.data["test_receipt"]
                origin_id = fork.data["test_origin_run_id"]
            artifact = self.workspace.artifact(receipt)
            tested = artifact["result"]
            evidence = tested["acceptance"]  # Legacy artifacts fail closed; never upgraded.
            origin = session.get(Run, origin_id)
            if (
                not origin
                or origin.task_id != run.task_id
                or (
                    evidence["schema"],
                    evidence["task_id"],
                    evidence["run_id"],
                    evidence["branch_id"],
                    evidence["test_run_id"],
                    artifact["tool"],
                    artifact["branch_id"],
                )
                != (
                    1,
                    run.task_id,
                    origin.id,
                    origin.branch_id,
                    receipt["external_id"],
                    "workspace.test",
                    origin.branch_id,
                )
            ):
                self.reject(run_id, "test_evidence_identity_mismatch")
            if not (origin.mode == "baseline" and origin.evaluation_run_id):
                effect = session.scalar(
                    select(Effect).where(
                        Effect.run_id == origin.id,
                        Effect.branch_id == origin.branch_id,
                        Effect.tool_name == "workspace.test",
                        Effect.external_receipt["external_id"].as_string()
                        == receipt["external_id"],
                    )
                )
                if not effect or effect.status != Status.CONFIRMED:
                    self.reject(run_id, "test_journal_not_confirmed")
            references = select(EffectReference.effect_id).where(
                EffectReference.branch_id == branch_id
            )
            uncertain = session.scalar(
                select(Effect)
                .where(
                    or_(Effect.branch_id == branch_id, Effect.effect_id.in_(references)),
                    Effect.status.in_([Status.UNKNOWN, Status.IN_FLIGHT]),
                    or_(Effect.effect_id != own_dispatch, Effect.status == Status.UNKNOWN)
                    if own_dispatch
                    else True,
                )
                .limit(1)
            )
            if uncertain:
                self.reject(run_id, "unresolved_effect_prevents_publication")
        if not (
            evidence["completed"] is True
            and tested["passed"] is True
            and tested["exit_code"] == 0
            and not tested["timed_out"]
            and tested["test_count"] > 0
            and not tested["fault_injected"]
        ):
            self.reject(run_id, "test_not_completed_and_passed")
        expected = evidence["manifest"]
        snapshot = self.workspace.snapshots.verified_source(evidence["snapshot_id"])
        if manifest(snapshot) != expected or fingerprint(expected) != evidence["workspace_hash"]:
            self.reject(run_id, "tested_snapshot_invalid")
        if payload_hash(evidence["configuration"]) != evidence["configuration_hash"] or evidence[
            "configuration"
        ] != self.workspace.sandbox.test_configuration(branch_id=branch_id, resolve_image=True):
            self.reject(run_id, "test_configuration_changed_retest_required")
        if manifest(self.workspace.workspaces.branch_path(branch_id)) != expected:
            self.reject(run_id, "workspace_changed_retest_required")
        return {
            "version": {
                "workspace_hash": evidence["workspace_hash"],
                "configuration_hash": evidence["configuration_hash"],
            },
            "test_receipt": receipt,
            "test_origin_run_id": origin_id,
            "snapshot_id": evidence["snapshot_id"],
        }

    def authorize(self, run_id, branch_id, payload, effect_id=None):
        proof = self.require(run_id, branch_id, own_dispatch=effect_id)
        if payload.get("_tested_version") != proof["version"]:
            self.reject(run_id, "publication_version_binding_changed")
        with self.sessions.begin() as session:
            session.add(
                RunEvent(
                    run_id=run_id,
                    effect_id=effect_id,
                    type="publication.authorized",
                    data=proof,
                )
            )
        return proof

"""Evaluation-only ablations; normal API tasks always use the full runtime."""

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from relayforge.journal.service import Journal
from relayforge.journal.state import (
    EffectClass,
    JournalError,
    RunCancelled,
    Status,
    idempotency_key,
    payload_hash,
)
from relayforge.models import Branch, Effect, Run, RunEvent
from relayforge.providers.mock_external import InjectedCrash, KnownNoEffect, MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.executor import (
    TOOLS,
    ApprovalRequired,
    OutcomeUnknown,
    PermissionDenied,
    ToolExecutor,
    ToolInput,
)
from relayforge.runtime.publication import PublicationGate, PublicationRejected


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supports_idempotency: bool = True
    supports_query: bool = True
    publish_tool: Literal["mock.create", "mock.notify"] = "mock.create"
    preauthorized: bool = False


class JournalAblation(ToolExecutor):
    def __init__(self, *args, query_enabled: bool, preauthorized: bool, **kwargs):
        super().__init__(*args, **kwargs)
        self.query_enabled = query_enabled
        self.preauthorized = preauthorized

    def execute(self, run_id, branch_id, action_id, tool_name, payload, fault="none"):
        try:
            return super().execute(run_id, branch_id, action_id, tool_name, payload, fault)
        except ApprovalRequired:
            if not self.preauthorized or tool_name not in {"mock.create", "mock.notify"}:
                raise
            bound_payload = payload
            if self.publications.required(run_id, action_id):
                proof = self.publications.require(run_id, branch_id)
                bound_payload = {**payload, "_tested_version": proof["version"]}
            with self.journal.sessions() as session:
                effect = session.scalar(
                    select(Effect).where(
                        Effect.run_id == run_id,
                        Effect.branch_id == branch_id,
                        Effect.logical_action_id == action_id,
                        Effect.payload_hash == payload_hash(bound_payload),
                    )
                )
                if not effect:
                    raise JournalError("Preauthorized fixture does not own this effect") from None
                identity = effect.effect_id
            self.approvals.decide(identity, "APPROVED")
            return super().execute(run_id, branch_id, action_id, tool_name, payload, fault)

    def reconcile(self, effect_id, *, owner_stopped=False):
        if self.query_enabled:
            return super().reconcile(effect_id, owner_stopped=owner_stopped)
        effect = self.journal.get(effect_id)
        if effect.status == Status.IN_FLIGHT and not owner_stopped:
            raise OutcomeUnknown("Active dispatch ownership cannot be transferred")
        if effect.reconciliation_method == "native_retransmission_in_flight" and not owner_stopped:
            raise OutcomeUnknown("Active native retransmission cannot be taken over")
        if effect.status in {Status.IN_FLIGHT, Status.UNKNOWN}:
            native = (
                effect.effect_class == EffectClass.IDEMPOTENT_WRITE
                and effect.tool_name == "mock.create"
                and self.external.supports_idempotency
            )
            return self.journal.outcome(
                effect_id,
                Status.UNKNOWN,
                None,
                method="native_idempotency_available"
                if native
                else "journal_ablation_no_external_query",
            )
        return effect


class BaselineExecutor:
    """Plain graph checkpoints + controlled provider tools, without Effect rows.

    Stable native idempotency keys are used when supported. Dispatch logs are
    ordinary instrumentation; a possibly committed non-idempotent call is not
    repeated without a previous protocol rejection. No external-query recovery.
    """

    def __init__(self, journal, external, workspace, *, preauthorized=False):
        # Graph event storage uses sessions; prepare/dispatch/outcome are never called.
        self.journal, self.external, self.workspace = journal, external, workspace
        self.preauthorized = preauthorized
        self.publications = PublicationGate(journal.sessions, workspace)

    def execute(self, run_id, branch_id, action_id, tool_name, payload, fault="none"):
        spec = TOOLS.get(tool_name)
        if not spec or spec.policy == "deny":
            raise PermissionDenied("Tool is not permitted")
        local = tool_name.startswith("workspace.")
        if local:
            self.workspace.validate(tool_name, branch_id, payload)
        else:
            ToolInput.model_validate(payload)
            if self.publications.required(run_id, action_id):
                proof = self.publications.require(run_id, branch_id)
                payload = {**payload, "_tested_version": proof["version"]}
        if spec.policy == "ask" and not self.preauthorized:
            raise ApprovalRequired("Common experimental permission guard requires authorization")
        if (
            spec.classification == EffectClass.IDEMPOTENT_WRITE
            and not self.external.supports_idempotency
        ):
            raise PermissionDenied("Provider idempotency guarantee is unavailable")
        key = idempotency_key(branch_id, action_id)
        native_idempotent = (
            spec.classification == EffectClass.IDEMPOTENT_WRITE
            and self.external.supports_idempotency
        )
        with self.journal.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            branch = session.get(Branch, branch_id)
            if (
                not run
                or not branch
                or run.branch_id != branch_id
                or run.task_id != branch.task_id
                or run.mode != "baseline"
                or not run.evaluation_run_id
            ):
                raise JournalError("Baseline is restricted to an owned evaluation run")
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                raise RunCancelled("Cancellation prevents a new tool call")
            if not local and not native_idempotent:
                previous = session.scalars(
                    select(RunEvent)
                    .where(
                        RunEvent.run_id == run_id,
                        RunEvent.type.in_(
                            ["baseline.tool_dispatch", "baseline.protocol_rejection"]
                        ),
                        RunEvent.data["action_id"].as_string() == action_id,
                    )
                    .order_by(RunEvent.id.desc())
                    .limit(1)
                ).first()
                if previous and previous.type == "baseline.tool_dispatch":
                    raise OutcomeUnknown(
                        "Baseline cannot safely retry a possibly committed non-idempotent call"
                    )
            if fault != "before_dispatch":
                session.add(
                    RunEvent(
                        run_id=run_id,
                        type="baseline.tool_dispatch",
                        data={
                            "action_id": action_id,
                            "tool": tool_name,
                            "payload_hash": payload_hash(payload),
                            "native_idempotency": native_idempotent,
                            "action_key": key,
                            "monotonic_ns": time.monotonic_ns(),
                        },
                    )
                )
        if fault == "before_dispatch":
            raise InjectedCrash("Crash before plain tool dispatch")
        if fault == "after_dispatch_before_call":
            raise InjectedCrash("Crash after plain dispatch before call")
        try:
            if local:
                return self.workspace.execute(
                    tool_name,
                    branch_id,
                    payload,
                    key,
                    fault,
                    context={"run_id": run_id, "task_id": run.task_id},
                )
            if self.publications.required(run_id, action_id):
                self.publications.authorize(run_id, branch_id, payload)
            return self.external.execute(
                key, payload_hash(payload), key if native_idempotent else None, fault
            )
        except PublicationRejected:
            raise
        except KnownNoEffect:
            with self.journal.sessions.begin() as session:
                session.add(
                    RunEvent(
                        run_id=run_id,
                        type="baseline.protocol_rejection",
                        data={
                            "action_id": action_id,
                            "outcome": "not_occurred",
                            "scope": "current_request_only",
                            "monotonic_ns": time.monotonic_ns(),
                        },
                    )
                )
            raise
        except Exception as exc:
            raise OutcomeUnknown("Plain tool outcome requires review; no blind retry") from exc


def executor_for(sessions, workspaces, settings, mode="full", config=None, evaluation_run_id=None):
    options = ExperimentConfig.model_validate(config or {})
    if mode not in {"baseline", "journal", "full"}:
        raise ValueError("Unknown evaluation mode")
    if (mode != "full" or options != ExperimentConfig()) and not evaluation_run_id:
        raise ValueError("Experimental configuration requires an evaluation identity")
    journal = Journal(sessions)
    external = MockExternal(
        settings.mock_external_path,
        supports_idempotency=options.supports_idempotency,
        supports_query=options.supports_query,
    )
    workspace = WorkspaceTools(workspaces)
    if mode == "baseline":
        return BaselineExecutor(journal, external, workspace, preauthorized=options.preauthorized)
    if mode == "full" and not evaluation_run_id:
        return ToolExecutor(journal, external, workspace)
    return JournalAblation(
        journal,
        external,
        workspace,
        query_enabled=mode == "full",
        preauthorized=options.preauthorized,
    )

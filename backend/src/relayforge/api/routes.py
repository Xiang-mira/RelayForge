from datetime import datetime
from typing import Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from relayforge.config import ROOT
from relayforge.evaluation.metrics import summarize
from relayforge.evaluation.runner import FAULTS
from relayforge.evaluation.service import EvaluationUnavailable, enqueue
from relayforge.journal.service import Journal
from relayforge.models import (
    Approval,
    Branch,
    Effect,
    EffectReference,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    RecoveryDecision,
    Run,
    RunEvent,
    Task,
)
from relayforge.policies.service import Approvals
from relayforge.providers.mock_external import MockExternal
from relayforge.providers.templates import TEMPLATES
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.cancellation import Cancellation
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.events import EventStream
from relayforge.runtime.evidence import RuntimeEvidence
from relayforge.runtime.executor import ToolExecutor
from relayforge.runtime.forks import Forks
from relayforge.runtime.presence import read_presence
from relayforge.runtime.tasks import Tasks
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning
from relayforge.workspaces.manager import Workspaces, reject_links


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    template: str = Field(default="fix_addition", json_schema_extra={"enum": list(TEMPLATES)})

    @field_validator("template")
    @classmethod
    def known_template(cls, value):
        if value not in TEMPLATES:
            raise ValueError("Unsupported deterministic repair template")
        return value

    fault: Literal[
        "none",
        "before_dispatch",
        "after_dispatch_before_call",
        "before_failure",
        "response_lost",
        "after_external_before_receipt",
        "sandbox_terminated",
    ] = "none"


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fault: Literal[
        "none",
        "before_dispatch",
        "after_dispatch_before_call",
        "before_failure",
        "response_lost",
        "after_external_before_receipt",
        "sandbox_terminated",
    ] = "none"


class View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TaskCreated(BaseModel):
    task_id: UUID
    run_id: UUID


class RunCreated(BaseModel):
    run_id: UUID


class ForkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checkpoint_id: str = Field(min_length=1, max_length=200)


class EventView(BaseModel):
    id: str
    source_event_id: int
    run_id: str
    effect_id: str | None
    type: str
    timestamp: datetime
    data: dict


class EventHistory(BaseModel):
    events: list[EventView]
    next_cursor: str


class CancelView(BaseModel):
    status: Literal["CANCELLED", "CANCEL_REQUESTED"]


class EvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    templates: list[str] = Field(
        default_factory=lambda: list(TEMPLATES), min_length=1, max_length=len(TEMPLATES)
    )
    faults: list[str] = Field(
        default_factory=lambda: list(FAULTS), min_length=1, max_length=len(FAULTS)
    )
    repeats: int = Field(default=1, ge=1, le=5)

    @field_validator("templates", "faults")
    @classmethod
    def known_values(cls, values, info):
        allowed = TEMPLATES if info.field_name == "templates" else FAULTS
        if len(set(values)) != len(values) or not set(values) <= set(allowed):
            raise ValueError("Unknown or duplicate evaluation case configuration")
        return values


class EvaluationQueued(BaseModel):
    suite_id: str
    evaluation_run_ids: list[str]
    status: Literal["QUEUED"]
    planned_observations: int


class TaskView(View):
    id: str
    title: str
    template: str
    created_at: datetime


class RunView(View):
    id: str
    execution_source: str
    task_id: str
    branch_id: str
    status: str
    fault: str
    mode: str
    evaluation_run_id: str | None
    owner: str | None
    lease_until: datetime | None
    test_exit_code: int | None
    created_at: datetime
    updated_at: datetime


class EffectView(View):
    effect_id: str
    run_id: str
    branch_id: str
    logical_action_id: str
    parent_effect_id: str | None
    tool_name: str
    effect_class: str
    status: str
    payload_hash: str
    payload_summary: dict
    external_receipt: dict | None
    reconciliation_method: str | None
    reconciliation_result: dict | None
    policy_decision: str
    approval_id: str | None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


def router(database, settings):
    def ready():
        if not database.ready():
            raise HTTPException(503, "PostgreSQL unavailable; no placeholder data returned")

    api = APIRouter(prefix="/api", dependencies=[Depends(ready)])

    @api.get("/worker")
    def worker_presence():
        return read_presence(settings)

    @api.get("/templates")
    def templates():
        return [{"id": name, "title": row.title} for name, row in TEMPLATES.items()]

    def require(session, entity, identity):
        found = session.get(entity, identity)
        if not found:
            raise HTTPException(404, "Record not found")
        return found

    def interactive(run):
        if run.evaluation_run_id:
            raise HTTPException(
                409, "Evaluation-owned runs are controlled by the experiment runner"
            )

    def tasks():
        return Tasks(
            database.sessions, Workspaces(settings.workspace_root), ROOT / "demo" / "sample_repo"
        )

    def executor():
        return ToolExecutor(
            Journal(database.sessions),
            MockExternal(settings.mock_external_path),
            WorkspaceTools(Workspaces(settings.workspace_root)),
        )

    @api.get("/overview")
    def overview():
        with database.sessions() as session:
            counts = {
                status: count
                for status, count in session.execute(
                    select(Run.status, func.count()).group_by(Run.status)
                )
            }
            unknown = session.scalar(
                select(func.count()).select_from(Effect).where(Effect.status == "UNKNOWN")
            )
            pending = session.scalar(
                select(func.count()).select_from(Approval).where(Approval.decision == "PENDING")
            )
        return {
            "runs_by_status": counts,
            "unknown_effects": unknown,
            "pending_approvals": pending,
            "provider": settings.model_provider,
        }

    @api.get("/effects", response_model=list[EffectView])
    def list_effects(
        status: Literal["PREPARED", "IN_FLIGHT", "CONFIRMED", "FAILED", "UNKNOWN"] | None = None,
        limit: int = Query(100, ge=1, le=500),
    ):
        with database.sessions() as session:
            query = select(Effect).order_by(Effect.created_at.desc()).limit(limit)
            if status:
                query = query.where(Effect.status == status)
            return list(session.scalars(query))

    @api.get("/approvals")
    def approvals():
        with database.sessions() as session:
            rows = session.scalars(
                select(Approval).order_by(Approval.created_at.desc()).limit(200)
            ).all()
            return [
                {
                    "id": row.id,
                    "effect_id": row.effect_id,
                    "branch_id": row.branch_id,
                    "payload_hash": row.payload_hash,
                    "decision": row.decision,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    @api.get("/evaluations")
    def evaluations():
        with database.sessions() as session:
            rows = session.execute(
                select(EvaluationRun, EvaluationSuite.name, EvaluationSuite.config)
                .join(EvaluationSuite, EvaluationSuite.id == EvaluationRun.suite_id)
                .order_by(EvaluationRun.created_at.desc())
                .limit(100)
            ).all()
            suites = {run.suite_id for run, _, _ in rows}
            observations = (
                session.execute(
                    select(EvaluationResult.observed, EvaluationRun.suite_id)
                    .join(EvaluationRun, EvaluationRun.id == EvaluationResult.evaluation_run_id)
                    .where(EvaluationRun.suite_id.in_(suites))
                ).all()
                if suites
                else []
            )
            summaries = {
                suite_id: summarize(
                    [
                        record
                        for record, identity in observations
                        if identity == suite_id
                        and record.get("scope") == "matrix"
                        and record.get("phase") == "COMPLETED"
                    ]
                )
                for suite_id in suites
            }
            return [
                {
                    "id": run.id,
                    "suite_id": run.suite_id,
                    "name": name,
                    "mode": run.mode,
                    "status": run.status,
                    "created_at": run.created_at,
                    "scope": "matrix" if config.get("matrix_version") == 1 else "mode_smoke",
                    "review_note": config.get("review_note"),
                    "planned_cases": len(config["templates"])
                    * len(config["faults"])
                    * config["repeats"]
                    if config.get("matrix_version") == 1
                    else None,
                    "template_count": len(config.get("templates", [])),
                    "fault_count": len(config.get("faults", [])),
                    "metrics": summaries[run.suite_id]["modes"].get(run.mode),
                }
                for run, name, config in rows
            ]

    @api.post("/evaluations/run", status_code=202, response_model=EvaluationQueued)
    def start_evaluation(body: EvaluationInput):
        try:
            return enqueue(database, settings, body.templates, body.faults, body.repeats)
        except EvaluationUnavailable:
            raise HTTPException(
                503, "Local evaluation environment unavailable; no job queued"
            ) from None

    @api.get("/evaluation-suites/{suite_id}/summary")
    def suite_summary(suite_id: str):
        with database.sessions() as session:
            suite = require(session, EvaluationSuite, suite_id)
            records = session.scalars(
                select(EvaluationResult.observed)
                .join(EvaluationRun, EvaluationRun.id == EvaluationResult.evaluation_run_id)
                .where(EvaluationRun.suite_id == suite_id)
            ).all()
            measured = [
                record
                for record in records
                if record.get("scope") == "matrix" and record.get("phase") == "COMPLETED"
            ]
            return {"suite_id": suite_id, "config": suite.config, "summary": summarize(measured)}

    @api.get("/evaluation-suites/{suite_id}/exports/{kind}")
    def suite_export(suite_id: str, kind: Literal["csv", "jsonl", "summary", "report"]):
        with database.sessions() as session:
            require(session, EvaluationSuite, suite_id)
        if str(UUID(suite_id)) != suite_id:
            raise ValueError("Canonical suite ID required")
        names = {
            "csv": ("results.csv", "text/csv"),
            "jsonl": ("results.jsonl", "application/x-ndjson"),
            "summary": ("summary.json", "application/json"),
            "report": ("report.md", "text/markdown"),
        }
        name, media_type = names[kind]
        path = ROOT / "artifacts/evaluations" / suite_id / name
        reject_links(path)
        if not path.is_file():
            raise HTTPException(404, "Measured export has not been generated")
        return FileResponse(path, media_type=media_type, filename=f"relayforge-{suite_id}-{name}")

    @api.get("/evaluations/{evaluation_id}/results")
    def evaluation_results(evaluation_id: str):
        with database.sessions() as session:
            require(session, EvaluationRun, evaluation_id)
            rows = session.scalars(
                select(EvaluationResult).where(EvaluationResult.evaluation_run_id == evaluation_id)
            ).all()
            return [
                {
                    "id": row.id,
                    "case_id": row.case_id,
                    "run_id": row.run_id,
                    "observed": row.observed,
                }
                for row in rows
            ]

    @api.post("/tasks", status_code=201, response_model=TaskCreated)
    def create_task(body: TaskInput):
        task_id, run_id = tasks().create(body.title, body.template, body.fault)
        return {"task_id": task_id, "run_id": run_id}

    @api.get("/tasks", response_model=list[TaskView])
    def list_tasks():
        with database.sessions() as session:
            return list(session.scalars(select(Task).order_by(Task.created_at.desc()).limit(100)))

    @api.get("/tasks/{task_id}", response_model=TaskView)
    def task_detail(task_id: str):
        with database.sessions() as session:
            return require(session, Task, task_id)

    @api.post("/tasks/{task_id}/runs", status_code=201, response_model=RunCreated)
    def create_run(task_id: str, body: RunInput):
        with database.sessions() as session:
            require(session, Task, task_id)
        return {"run_id": tasks().start(task_id, body.fault)}

    @api.get("/runs", response_model=list[RunView])
    def list_runs():
        with database.sessions() as session:
            return list(session.scalars(select(Run).order_by(Run.created_at.desc()).limit(100)))

    @api.get("/runs/{run_id}", response_model=RunView)
    def run_detail(run_id: str):
        with database.sessions() as session:
            return require(session, Run, run_id)

    @api.get("/runs/{run_id}/effects", response_model=list[EffectView])
    def run_effects(run_id: str):
        with database.sessions() as session:
            require(session, Run, run_id)
            return list(
                session.scalars(
                    select(Effect).where(Effect.run_id == run_id).order_by(Effect.created_at)
                )
            )

    @api.get("/effects/{effect_id}", response_model=EffectView)
    def effect_detail(effect_id: str):
        with database.sessions() as session:
            return require(session, Effect, effect_id)

    @api.get("/effects/{effect_id}/decisions")
    def effect_decisions(effect_id: str):
        with database.sessions() as session:
            require(session, Effect, effect_id)
            rows = session.scalars(
                select(RecoveryDecision)
                .where(RecoveryDecision.effect_id == effect_id)
                .order_by(RecoveryDecision.created_at)
            ).all()
            return [
                {
                    "id": row.id,
                    "decision": row.decision,
                    "evidence": row.evidence,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    @api.get("/effects/{effect_id}/approval")
    def effect_approval(effect_id: str):
        with database.sessions() as session:
            effect = require(session, Effect, effect_id)
            row = session.get(Approval, effect.approval_id) if effect.approval_id else None
            return (
                {
                    "id": row.id,
                    "effect_id": row.effect_id,
                    "branch_id": row.branch_id,
                    "payload_hash": row.payload_hash,
                    "decision": row.decision,
                    "created_at": row.created_at,
                }
                if row
                else None
            )

    @api.post("/effects/{effect_id}/approve")
    def approve(effect_id: str):
        with database.sessions() as session:
            interactive(require(session, Run, require(session, Effect, effect_id).run_id))
        Approvals(database.sessions).decide(effect_id, "APPROVED")
        return {"decision": "APPROVED"}

    @api.post("/effects/{effect_id}/reject")
    def reject(effect_id: str):
        with database.sessions() as session:
            interactive(require(session, Run, require(session, Effect, effect_id).run_id))
        Approvals(database.sessions).decide(effect_id, "REJECTED")
        return {"decision": "REJECTED"}

    @api.post("/effects/{effect_id}/reconcile", response_model=EffectView)
    def reconcile(effect_id: str):
        with database.sessions() as session:
            effect = require(session, Effect, effect_id)
            interactive(require(session, Run, effect.run_id))
            status, tool = effect.status, effect.tool_name
        controlled = executor()
        if status == "IN_FLIGHT":
            if tool == "workspace.test":
                raise HTTPException(409, "Sandbox quiescence is not yet verified")
            try:
                with LocalWorkerLock(ROOT / ".local" / "worker.lock"):
                    return controlled.reconcile(effect_id, owner_stopped=True)
            except WorkerAlreadyRunning:
                raise HTTPException(
                    409, "A worker is active; dispatch ownership is not transferred"
                ) from None
        return controlled.reconcile(effect_id)

    @api.post("/runs/{run_id}/replay", response_model=RunView)
    def replay(run_id: str):
        with database.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if not run:
                raise HTTPException(404, "Record not found")
            interactive(run)
            if run.execution_config.get("controller") == "mcp":
                raise HTTPException(409, "Controlled tasks resume through their MCP context")
            if run.status in {"QUEUED", "PREPARING", "RUNNING", "CANCEL_REQUESTED"}:
                raise HTTPException(409, "Run already pending or executing")
            run.status = "QUEUED"
            session.add(
                RunEvent(
                    run_id=run_id,
                    type="run.replay_requested",
                    data={"mode": "reuse_confirmed_effects"},
                )
            )
            session.flush()
            return RunView.model_validate(run)

    @api.post("/runs/{run_id}/cancel", response_model=CancelView)
    def cancel(run_id: str):
        with database.sessions() as session:
            interactive(require(session, Run, run_id))
        return {"status": Cancellation(database.sessions).request(run_id)}

    @api.get("/runs/{run_id}/events/history", response_model=EventHistory)
    def history(
        run_id: str,
        after: int = Query(0, ge=0, le=9223372036854775807),
        limit: int = Query(200, ge=1, le=500),
    ):
        with database.sessions() as session:
            require(session, Run, run_id)
        stream = EventStream(database.sessions)
        if not stream.validate_cursor(run_id, after):
            raise HTTPException(409, "Unknown or unrelated event cursor; request history from zero")
        rows = stream.read(run_id, after, limit)
        return {"events": rows, "next_cursor": rows[-1]["id"] if rows else str(after)}

    @api.get(
        "/runs/{run_id}/events",
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}}},
    )
    async def events(
        run_id: str,
        request: Request,
        after: int = Query(0, ge=0, le=9223372036854775807),
        once: bool = False,
    ):
        def validate():
            with database.sessions() as session:
                require(session, Run, run_id)

        await anyio.to_thread.run_sync(validate)
        raw = request.headers.get("last-event-id", str(after))
        if not raw.isdecimal() or int(raw) > 9223372036854775807:
            raise HTTPException(422, "Last-Event-ID must be a nonnegative durable cursor")
        cursor = int(raw)
        stream = EventStream(database.sessions)
        if not await anyio.to_thread.run_sync(stream.validate_cursor, run_id, cursor):
            raise HTTPException(409, "Unknown or unrelated event cursor; request history from zero")

        async def generate():
            nonlocal cursor
            idle = 0
            while not await request.is_disconnected():
                try:
                    rows = await anyio.to_thread.run_sync(stream.read, run_id, cursor)
                except SQLAlchemyError:
                    yield 'event: transport_error\ndata: {"detail":"Database temporarily unavailable; reconnect with last cursor"}\n\n'
                    return
                for row in rows:
                    cursor = int(row["id"])
                    yield stream.frame(row)
                if once:
                    if not rows:
                        yield ": no new events\n\n"
                    return
                idle = 0 if rows else idle + 1
                if idle >= 30:
                    yield ": keep-alive\n\n"
                    idle = 0
                await anyio.sleep(0.3)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @api.get("/runs/{run_id}/evidence")
    def evidence(run_id: str):
        try:
            return RuntimeEvidence(database.sessions, settings).view(run_id)
        except LookupError:
            raise HTTPException(404, "Record not found") from None

    @api.get("/runs/{run_id}/artifacts")
    def artifacts(run_id: str):
        with database.sessions() as session:
            run = require(session, Run, run_id)
            branch = session.get(Branch, run.branch_id)
            ancestors = set()
            while branch and branch.parent_id and branch.parent_id not in ancestors:
                ancestors.add(branch.parent_id)
                branch = session.get(Branch, branch.parent_id)
            effects = session.scalars(
                select(Effect).where(Effect.run_id == run_id, Effect.status == "CONFIRMED")
            ).all()
        controlled = executor()
        provider = controlled.workspace
        rows = [
            {
                "effect_id": effect.effect_id,
                "artifact_id": effect.external_receipt["external_id"],
                "tool": effect.tool_name,
                "result": provider.result(effect.external_receipt),
                "readonly": False,
                "source_run_id": run_id,
            }
            for effect in effects
            if effect.tool_name in {"workspace.test", "workspace.diff"}
        ]
        if ancestors or run.mode == "baseline":
            with postgres_checkpointer(settings) as saver:
                state = (
                    Forks(controlled, saver)
                    .graph.get_state({"configurable": {"thread_id": run_id}})
                    .values
                )
            for field, tool in [
                ("test_receipt", "workspace.test"),
                ("diff_receipt", "workspace.diff"),
            ]:
                receipt = state.get(field)
                if not receipt or receipt.get("provider") != "managed_workspace":
                    continue
                if run.mode == "baseline":
                    artifact = provider.artifact(receipt)
                    if artifact["branch_id"] != run.branch_id or artifact["tool"] != tool:
                        raise ValueError("Checkpoint artifact binding mismatch")
                    rows.append(
                        {
                            "effect_id": None,
                            "artifact_id": receipt["external_id"],
                            "tool": tool,
                            "result": artifact["result"],
                            "readonly": False,
                            "source_run_id": run_id,
                        }
                    )
                    continue
                with database.sessions() as session:
                    origin = session.scalar(
                        select(Effect).where(
                            Effect.branch_id.in_(ancestors),
                            Effect.tool_name == tool,
                            Effect.status == "CONFIRMED",
                            Effect.external_receipt["external_id"].as_string()
                            == receipt["external_id"],
                        )
                    )
                if origin:
                    rows.append(
                        {
                            "effect_id": origin.effect_id,
                            "artifact_id": receipt["external_id"],
                            "tool": tool,
                            "result": provider.result(receipt),
                            "readonly": True,
                            "source_run_id": origin.run_id,
                        }
                    )
        return rows

    @api.get("/branches")
    def branches():
        with database.sessions() as session:
            rows = session.scalars(
                select(Branch).order_by(Branch.created_at.desc()).limit(100)
            ).all()
            return [
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "parent_id": row.parent_id,
                    "checkpoint_id": row.checkpoint_id,
                }
                for row in rows
            ]

    @api.get("/runs/{run_id}/checkpoints")
    def checkpoints(run_id: str):
        with postgres_checkpointer(settings) as saver:
            return Forks(executor(), saver).history(run_id)

    @api.post("/runs/{run_id}/fork", status_code=201, response_model=RunCreated)
    def fork(run_id: str, body: ForkInput):
        with database.sessions() as session:
            source = require(session, Run, run_id)
            interactive(source)
            if source.execution_config.get("controller") == "mcp":
                raise HTTPException(409, "Controlled tasks do not use fixed-graph Fork")
        with postgres_checkpointer(settings) as saver:
            return {"run_id": Forks(executor(), saver).create(run_id, body.checkpoint_id)}

    @api.get("/runs/{run_id}/effect-references")
    def references(run_id: str):
        with database.sessions() as session:
            run = require(session, Run, run_id)
            effects = session.scalars(
                select(Effect)
                .join(EffectReference, EffectReference.effect_id == Effect.effect_id)
                .where(EffectReference.branch_id == run.branch_id)
            ).all()
            return [
                {"readonly": True, "effect": EffectView.model_validate(effect).model_dump()}
                for effect in effects
            ]

    return api

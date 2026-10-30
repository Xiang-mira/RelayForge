"""Bounded, durable tool sessions over the existing task/executor services."""

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from relayforge.config import ROOT
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, JournalError, Status, payload_hash
from relayforge.models import Approval, Branch, Effect, Run, RunEvent
from relayforge.providers.mock_external import KnownNoEffect, MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.acceptance import TaskAcceptance
from relayforge.runtime.catalog import CONTROLLED_TEMPLATE, PYTEST_TEMPLATES
from relayforge.runtime.evidence import RuntimeEvidence
from relayforge.runtime.executor import (
    ApprovalRequired,
    OutcomeUnknown,
    PermissionDenied,
    ToolExecutor,
)
from relayforge.runtime.publication import PublicationRejected
from relayforge.runtime.tasks import Tasks
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning
from relayforge.workspaces.fingerprint import fingerprint, manifest
from relayforge.workspaces.manager import Workspaces

OPERATIONS = Literal["apply_patch", "run_tests", "finish_task"]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Begin(Arguments):
    title: str = Field(default="MCP shipping feedback", min_length=1, max_length=200)
    context_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    agent_session_ref: str = Field(default="stdio-client", min_length=1, max_length=128)
    template: Literal["mcp_feedback", "mcp_header_parser", "mcp_csv_records", "mcp_command_words", "mcp_query_codec"] = CONTROLLED_TEMPLATE


class Context(Arguments):
    context_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class Prepare(Context):
    request_key: str = Field(min_length=1, max_length=80)
    operation: OPERATIONS


class Action(Context):
    action_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class Read(Context):
    path: str = Field(default="calculator.py", min_length=1, max_length=200)


class Patch(Action):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=65536)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def no_context_in_source(self):
        if self.context_id in self.content:
            raise ValueError("Task authorization must not be persisted in source")
        return self


class Tests(Action):
    configuration: Literal["unittest", "pytest"] = "unittest"


SCHEMAS = {
    "begin_task": Begin, "prepare_action": Prepare, "inspect_repository": Context,
    "read_file": Read, "apply_patch": Patch, "run_tests": Tests,
    "inspect_diff": Context, "get_execution_status": Context, "finish_task": Action,
}


class Refused(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class ControlledTasks:
    """Business layer, not an MCP implementation. Counters and identities survive restart."""

    def __init__(self, sessions, settings, *, limits=None, publish_tool="mock.create"):
        self.sessions, self.settings = sessions, settings
        self.workspaces = Workspaces(settings.workspace_root)
        self.executor = ToolExecutor(
            Journal(sessions), MockExternal(settings.mock_external_path),
            WorkspaceTools(self.workspaces),
        )
        self.tasks = Tasks(sessions, self.workspaces, ROOT / "demo/sample_repo")
        # Match Worker.main and the existing API reconciler, even when storage is overridden.
        self.lock_path = ROOT / ".local" / "worker.lock"
        self.limits = limits or {"calls": 40, "tests": 4, "seconds": 600}
        if publish_tool not in {"mock.create", "mock.notify"}:
            raise ValueError("Only local mock publication is supported")
        self.publish_tool = publish_tool

    @staticmethod
    def result(status, message, run=None, *, data=None, action_id=None, evidence=None, retryable=False):
        return {
            "status": status, "message": message, "data": data or {},
            "task_id": run.task_id if run else None, "run_id": run.id if run else None,
            "action_id": action_id, "evidence": evidence, "retryable": retryable,
        }

    def context(self, token, *, consume=False, terminal_read=False):
        if not isinstance(token, str) or len(token) != 64:
            raise Refused("INVALID_CONTEXT", "A valid task context is required")
        with self.sessions.begin() as session:
            run = session.scalar(select(Run).where(
                Run.execution_config["context_hash"].as_string() == digest(token)
            ).with_for_update())
            if not run or run.execution_config.get("controller") != "mcp":
                raise Refused("INVALID_CONTEXT", "Unknown or unauthorized task context")
            if run.status not in {"CONTROLLED", "NEEDS_REVIEW"} and not terminal_read:
                raise Refused("TASK_FINISHED", "Task is finished or no longer controlled")
            branch = session.get(Branch, run.branch_id)
            path = self.workspaces.branch_path(run.branch_id)
            if not branch or branch.task_id != run.task_id or not path.is_dir():
                raise Refused("DENIED", "Authorized workspace is unavailable")
            config = dict(run.execution_config)
            if consume:
                if config["expires_at"] <= datetime.now(UTC).timestamp() or config["calls_left"] <= 0:
                    raise Refused("BUDGET_EXCEEDED", "Task call or time budget exhausted")
                config["calls_left"] -= 1
                run.execution_config = config
            session.flush()
            session.expunge(run)
            return run

    def budget(self, run):
        cfg = run.execution_config
        return {"calls_left": cfg["calls_left"], "tests_left": cfg["tests_left"],
                "seconds_left": max(0, int(cfg["expires_at"] - datetime.now(UTC).timestamp()))}

    def invoke(self, operation, arguments):
        started = datetime.now(UTC).isoformat()
        result = self._invoke(operation, arguments)
        if result.get("run_id"):
            RuntimeEvidence(self.sessions, self.settings).record_call(operation, arguments, result, started)
        return result

    def _invoke(self, operation, arguments):
        run = None
        action_id = arguments.get("action_id") if isinstance(arguments, dict) else None
        try:
            with LocalWorkerLock(self.lock_path):
                if operation not in SCHEMAS:
                    raise Refused("DENIED", "Tool is not registered")
                if operation == "begin_task":
                    parsed = Begin.model_validate(arguments)
                    if parsed.context_id:
                        run = self.context(parsed.context_id)  # Never resets budgets.
                        return self.result("OK", "Existing authorized context bound", run,
                                           data=self.context_data(run, parsed.context_id))
                    token = secrets.token_hex(32)
                    _, run_id = self.tasks.create(parsed.title, parsed.template, controlled=True)
                    with self.sessions.begin() as session:
                        run = session.get(Run, run_id)
                        run.execution_config = {
                            "controller": "mcp", "context_hash": digest(token),
                            "calls_left": self.limits["calls"], "tests_left": self.limits["tests"],
                            "expires_at": datetime.now(UTC).timestamp() + self.limits["seconds"],
                            "test_receipt": None, "test_actions": [], "publish_tool": self.publish_tool,
                            "test_configuration": "pytest" if parsed.template in PYTEST_TEMPLATES else "unittest",
                            "agent_session_ref_hash": digest(parsed.agent_session_ref),
                        }
                        session.add(RunEvent(run_id=run.id, type="controlled.context_created",
                                             data={"transport": "mcp", "budget": self.limits}))
                        session.flush()
                        session.expunge(run)
                    return self.result("OK", "Bounded feedback task created", run,
                                       data=self.context_data(run, token))
                # Charge valid-context attempts even when their path/arguments are invalid.
                token = arguments.get("context_id")
                run = self.context(token, consume=operation != "get_execution_status",
                                   terminal_read=operation == "get_execution_status")
                parsed = SCHEMAS[operation].model_validate(arguments)
                data, evidence = self.dispatch(operation, parsed, run)
                status = "FAILED" if operation == "run_tests" and not data["passed"] else "OK"
                return self.result(status, "Test failed" if status == "FAILED" else "Operation completed",
                                   run, data=data, evidence=evidence, action_id=action_id)
        except Refused as exc:
            return self.result(exc.status, exc.message, run, action_id=action_id)
        except Conflict:
            return self.result("CONFLICT", "Action identity conflicts with recorded parameters", run,
                               action_id=action_id)
        except ApprovalRequired:
            return self.result("NEEDS_APPROVAL", "Branch and version-specific approval required", run,
                               action_id=action_id)
        except PublicationRejected as exc:
            return self.result("NEEDS_REVALIDATION", str(exc), run, action_id=action_id)
        except OutcomeUnknown:
            return self.result("UNKNOWN", "Outcome or acceptance unresolved; inspect status before retrying",
                               run, action_id=action_id)
        except KnownNoEffect:
            return self.result("CONFLICT", "Version precondition rejected before mutation", run,
                               action_id=action_id)
        except (PermissionDenied, ValueError, OSError, ValidationError):
            return self.result("DENIED", "Invalid bounded input or unauthorized workspace access", run,
                               action_id=action_id)
        except JournalError:
            return self.result("FAILED", "Runtime guard refused operation; inspect its recorded state", run,
                               action_id=action_id)
        except WorkerAlreadyRunning:
            return self.result("BUSY", "Runtime owns another operation; no dispatch occurred", run,
                               action_id=action_id, retryable=True)

    def context_data(self, run, token):
        return {"context_id": token, "task_id": run.task_id, "run_id": run.id,
                "scope": {"read": ["calculator.py", "test_calculator.py"],
                          "write": ["calculator.py"],
                          "test_configuration": run.execution_config.get("test_configuration", "unittest"),
                          "publication": run.execution_config["publish_tool"]},
                "budget": self.budget(run)}

    def update(self, run_id, **values):
        with self.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            run.execution_config = {**run.execution_config, **values}

    def bind_action(self, run, action_id, operation, parameters):
        conflict = False
        with self.sessions.begin() as session:
            record = session.scalar(select(RunEvent).where(
                RunEvent.run_id == run.id, RunEvent.type == "controlled.action",
                RunEvent.data["action_id"].as_string() == action_id,
            ).with_for_update())
            if not record or record.data["operation"] != operation:
                raise Refused("DENIED", "Action does not belong to this context and operation")
            requested = payload_hash(parameters)
            bound = session.scalar(select(RunEvent).where(
                RunEvent.run_id == run.id, RunEvent.type == "controlled.action_bound",
                RunEvent.data["action_id"].as_string() == action_id,
            ))
            old = bound.data["parameters_hash"] if bound else None
            if old and old != requested:
                conflict = True
                session.add(RunEvent(run_id=run.id, type="controlled.action_conflict",
                                     data={"action_id": action_id, "requested_hash": requested}))
            elif not bound:
                session.add(RunEvent(run_id=run.id, type="controlled.action_bound",
                                     data={"action_id": action_id, "parameters_hash": requested}))
        if conflict:
            raise Conflict("Action parameters changed")

    def recover_action(self, run, action_id):
        with self.sessions() as session:
            effect = session.scalar(select(Effect).where(
                Effect.run_id == run.id, Effect.logical_action_id == action_id
            ))
        if effect and effect.status in {Status.UNKNOWN, Status.IN_FLIGHT}:
            if effect.tool_name == "workspace.test":
                raise OutcomeUnknown("Interrupted container requires human quiescence verification")
            # This call holds the same OS ownership lock as the ordinary worker.
            recovered = self.executor.reconcile(effect.effect_id, owner_stopped=True)
            if recovered.status != Status.CONFIRMED:
                raise OutcomeUnknown("No sufficient provider evidence")

    def tool(self, run, action_id, tool, payload):
        return self.executor.execute(run.id, run.branch_id, action_id, tool, payload)

    def read(self, run, path):
        receipt = self.tool(run, str(uuid4()), "workspace.read", {"path": path})
        content = self.executor.workspace.result(receipt)["content"]
        if len(content.encode()) > 65536 or "\x00" in content:
            raise Refused("DENIED", "Text file exceeds output bounds or is binary")
        raw = self.workspaces.file(run.branch_id, path).read_bytes()
        return {"path": path, "content": content, "sha256": hashlib.sha256(raw).hexdigest()}, receipt

    def dispatch(self, operation, args, run):
        if operation == "prepare_action":
            key = digest(args.request_key)
            with self.sessions.begin() as session:
                old = session.scalar(select(RunEvent).where(
                    RunEvent.run_id == run.id, RunEvent.type == "controlled.action",
                    RunEvent.data["request_key_hash"].as_string() == key,
                ))
                if old:
                    if old.data["operation"] != args.operation:
                        raise Conflict("Request key belongs to another operation")
                    return {"action_id": old.data["action_id"]}, None
                action = str(uuid4())
                session.add(RunEvent(run_id=run.id, type="controlled.action", data={
                    "action_id": action, "operation": args.operation, "request_key_hash": key,
                }))
                return {"action_id": action}, None
        if operation == "inspect_repository":
            files = []
            for path in ("calculator.py", "test_calculator.py"):
                data, _ = self.read(run, path)
                files.append({"path": path, "bytes": len(data["content"].encode()), "sha256": data["sha256"]})
            return {"files": files}, None
        if operation == "read_file":
            return self.read(run, args.path)
        if operation == "inspect_diff":
            with self.sessions() as session:
                snapshot_id = session.get(Branch, run.branch_id).initial_snapshot_id
            initial = self.executor.workspace.snapshots.verified_source(snapshot_id)
            receipt = self.tool(run, str(uuid4()), "workspace.diff", {
                "path": "calculator.py", "original": (initial / "calculator.py").read_text(encoding="utf-8"),
            })
            result = self.executor.workspace.result(receipt)
            return {"diff": result["diff"][:16384], "truncated": len(result["diff"]) > 16384,
                    "workspace_hash": fingerprint(manifest(self.workspaces.branch_path(run.branch_id)))}, receipt
        if operation == "get_execution_status":
            return self.status(run), None
        parameters = args.model_dump(exclude={"context_id", "action_id"})
        self.bind_action(run, args.action_id, operation, parameters)
        self.recover_action(run, args.action_id)
        with self.sessions() as session:
            uncertain = session.scalar(select(Effect).where(
                Effect.run_id == run.id, Effect.status.in_([Status.UNKNOWN, Status.IN_FLIGHT])
            ).limit(1))
        if uncertain:
            raise OutcomeUnknown("Unresolved operation prevents further managed mutations")
        if operation == "apply_patch":
            receipt = self.tool(run, args.action_id, "workspace.write", parameters)
            return self.executor.workspace.result(receipt), receipt
        if operation == "run_tests":
            if args.configuration != run.execution_config.get("test_configuration", "unittest"):
                raise Refused("DENIED", "Test profile must match the registered task")
            self.reserve_test(run, args.action_id)
            receipt = self.tool(run, args.action_id, "workspace.test", {})
            self.update(run.id, test_receipt=receipt)
            tested = self.executor.workspace.result(receipt)
            with self.sessions.begin() as session:
                session.get(Run, run.id).test_exit_code = tested["exit_code"]
            return self.bounded_test(tested), receipt
        if operation == "finish_task":
            proof = self.executor.publications.require(run.id, run.branch_id)
            receipt = self.tool(run, args.action_id, run.execution_config["publish_tool"],
                                {"subject": "controlled-feedback-result"})
            result = TaskAcceptance(self.executor).complete(run.id, run.branch_id, proof["test_receipt"])
            return {"result": result, "tested_version": proof}, receipt
        raise Refused("DENIED", "Unsupported controlled operation")

    def reserve_test(self, run, action_id):
        with self.sessions.begin() as session:
            current = session.get(Run, run.id, with_for_update=True)
            cfg = dict(current.execution_config)
            actions = list(cfg["test_actions"])
            if action_id in actions and action_id != actions[-1]:
                raise Refused("CONFLICT", "An older test action cannot replace the latest test evidence")
            if action_id not in actions:
                if cfg["tests_left"] <= 0 or cfg["expires_at"] - datetime.now(UTC).timestamp() < 35:
                    raise Refused("BUDGET_EXCEEDED", "Test count or time budget exhausted")
                actions.append(action_id)
                cfg.update(test_actions=actions, tests_left=cfg["tests_left"] - 1, test_receipt=None)
                current.execution_config = cfg

    def status(self, run):
        with self.sessions() as session:
            current = session.get(Run, run.id)
            effects = session.scalars(select(Effect).where(Effect.run_id == run.id).order_by(Effect.created_at)).all()
            pending = session.scalars(select(Approval).join(Effect, Effect.approval_id == Approval.id).where(
                Effect.run_id == run.id, Approval.decision == "PENDING"
            )).all()
            events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)
                                     .order_by(RunEvent.id.desc()).limit(12)).all()
            receipt = current.execution_config.get("test_receipt")
            return {"state": current.status, "branch_id": current.branch_id,
                    "budget": self.budget(current),
                    "operations": [{"action_id": e.logical_action_id, "effect_id": e.effect_id,
                                    "tool": e.tool_name, "status": e.status, "receipt": e.external_receipt}
                                   for e in effects[:80]],
                    "unknown": [e.effect_id for e in effects if e.status in {Status.UNKNOWN, Status.IN_FLIGHT}],
                    "pending_approvals": [a.effect_id for a in pending],
                    "events": [{"type": e.type, "id": e.id} for e in events],
                    "last_test": self.bounded_test(self.executor.workspace.result(receipt)) if receipt else None}

    @staticmethod
    def bounded_test(tested):
        result = dict(tested)
        for field in ("output", "stdout", "stderr"):
            if field in result:
                result[field] = result[field][:16384]
        return result

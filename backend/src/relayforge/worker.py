import argparse
import os
import time
from datetime import timedelta
from uuid import uuid4

import psycopg
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from relayforge.config import ROOT, get_settings
from relayforge.db import Database
from relayforge.evaluation.modes import ExperimentConfig, executor_for
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, JournalError, RunCancelled, Status
from relayforge.models import Branch, Effect, Run, RunEvent, Task, now
from relayforge.providers.coding import ProviderUnavailable, coding_provider
from relayforge.providers.mock_external import InjectedCrash, KnownNoEffect, MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ApprovalRequired, OutcomeUnknown, ToolExecutor
from relayforge.runtime.graph import build_graph
from relayforge.runtime.presence import WorkerPresence
from relayforge.runtime.publication import PublicationRejected
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning
from relayforge.workspaces.manager import Workspaces


class Worker:
    def __init__(self, database: Database, settings, lock: LocalWorkerLock):
        self.database = database
        self.settings = settings
        self.owner = str(uuid4())
        self.current_run = None
        self.lock = lock
        self.workspaces = Workspaces(settings.workspace_root)
        self.executor = ToolExecutor(
            Journal(database.sessions),
            MockExternal(settings.mock_external_path),
            WorkspaceTools(self.workspaces),
        )

    def run_once(self, target_run: str | None = None):
        if not self.lock.locked:
            raise RuntimeError("Worker must hold the local OS lock before claiming a run")
        # A lease is visibility, not fencing. The OS lock covers the whole execution.
        with self.database.sessions.begin() as session:
            run = session.scalar(
                select(Run)
                .where(
                    Run.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
                    Run.id == target_run if target_run else Run.evaluation_run_id.is_(None),
                )
                .order_by(Run.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not run:
                return False
            recovering = run.status == "RUNNING"
            cancel_pending = run.status == "CANCEL_REQUESTED"
            run.status = "CANCEL_REQUESTED" if cancel_pending else "RUNNING"
            run.owner = self.owner
            run.lease_until = now() + timedelta(seconds=60)
            task = session.get(Task, run.task_id)
            mode, execution_config, evaluation_identity = (
                run.mode,
                run.execution_config,
                run.evaluation_run_id,
            )
            state = {
                "run_id": run.id,
                "branch_id": run.branch_id,
                "template": task.template,
                "fault": run.fault,
                "snapshot_id": session.get(Branch, run.branch_id).initial_snapshot_id,
                "publish_tool": (execution_config or {}).get("publish_tool", "mock.create"),
            }
            session.add(
                RunEvent(
                    run_id=run.id,
                    type="worker.claimed",
                    data={"owner": self.owner, "recovering": recovering},
                )
            )
        self.current_run = state["run_id"]
        try:
            ExperimentConfig.model_validate(execution_config or {})
            self.executor = executor_for(
                self.database.sessions,
                self.workspaces,
                self.settings,
                mode,
                execution_config,
                evaluation_identity,
            )
            if cancel_pending:
                with self.database.sessions() as session:
                    uncertain = session.scalars(
                        select(Effect).where(
                            Effect.run_id == state["run_id"], Effect.status == Status.IN_FLIGHT
                        )
                    ).all()
                for effect in uncertain:
                    self.executor.journal.outcome(
                        effect.effect_id,
                        Status.UNKNOWN,
                        None,
                        method="cancel_does_not_undo_dispatched_effect",
                    )
                self.mark(state["run_id"], "CANCELLED", "cancelled_before_resume")
                return True
            self.recover(state["run_id"])
            with postgres_checkpointer(self.settings) as saver:
                graph = build_graph(self.executor, saver, coding_provider(self.settings))
                config = {"configurable": {"thread_id": state["run_id"]}}
                snapshot = graph.get_state(config)
                if snapshot.values and not snapshot.next:
                    # Replaying a completed graph cannot be turned into a fresh coding claim.
                    self.executor.publications.require(state["run_id"], state["branch_id"])
                    self.mark(
                        state["run_id"],
                        "SUCCEEDED",
                        "verified_historical_acceptance",
                    )
                else:
                    graph.invoke(None if snapshot.values else state, config)
        except RunCancelled:
            self.mark(state["run_id"], "CANCELLED", "cooperative_cancel")
        except (OutcomeUnknown, ApprovalRequired, Conflict, PublicationRejected) as exc:
            self.mark(state["run_id"], "NEEDS_REVIEW", type(exc).__name__)
        except JournalError:
            self.mark(state["run_id"], "FAILED", "runtime_guard")
        except KnownNoEffect:
            self.mark(state["run_id"], "FAILED", "provider_rejection_before_effect")
        except ValueError:
            self.mark(state["run_id"], "FAILED", "invalid_sample_or_artifact")
        except ProviderUnavailable:
            self.mark(state["run_id"], "FAILED", "coding_provider_unavailable")
        finally:
            self.current_run = None
        return True

    def recover(self, run_id: str):
        if not self.lock.locked:
            raise RuntimeError("Recovery requires the exclusive local worker lock")
        with self.database.sessions() as session:
            effects = session.scalars(
                select(Effect).where(
                    Effect.run_id == run_id,
                    Effect.status.in_([Status.IN_FLIGHT, Status.UNKNOWN, Status.FAILED]),
                )
            ).all()
        for effect in effects:
            if effect.status == Status.FAILED:
                if (effect.reconciliation_result or {}).get("outcome") != "not_occurred":
                    raise OutcomeUnknown("Safe retry lacks absence evidence")
                self.executor.journal.outcome(
                    effect.effect_id,
                    Status.PREPARED,
                    "not_occurred",
                    method="explicit_retry_after_proven_absence",
                )
                continue
            # Until container quiescence is implemented/proven, never infer a test outcome.
            if effect.tool_name == "workspace.test":
                self.executor.journal.outcome(
                    effect.effect_id,
                    Status.UNKNOWN,
                    None,
                    method="interrupted_sandbox_without_completion_receipt",
                )
                raise OutcomeUnknown(
                    "Interrupted sandbox requires container quiescence verification"
                )
            recovered = self.executor.reconcile(effect.effect_id, owner_stopped=True)
            if recovered.status == Status.UNKNOWN:
                if self.executor.can_resume_native(recovered):
                    continue
                raise OutcomeUnknown("Provider evidence remains insufficient")
            if recovered.status == Status.FAILED:
                self.executor.journal.outcome(
                    effect.effect_id,
                    Status.PREPARED,
                    "not_occurred",
                    method="safe_retry_after_absence",
                )

    def mark(self, run_id: str, status: str, reason: str):
        with self.database.sessions.begin() as session:
            run = session.get(Run, run_id, with_for_update=True)
            if run.status in {"CANCELLED", "CANCEL_REQUESTED"}:
                status = "CANCELLED"
            run.status = status
            run.lease_until = None
            session.add(
                RunEvent(
                    run_id=run_id, type="run.status", data={"status": status, "reason": reason}
                )
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--run-id", help="Limit this worker invocation to one local run")
    args = parser.parse_args()
    settings = get_settings()
    database = Database(settings)
    if not database.ready():
        raise SystemExit("PostgreSQL unavailable; worker did not start")
    try:
        with LocalWorkerLock(ROOT / ".local" / "worker.lock") as lock:
            worker = Worker(database, settings, lock)
            with WorkerPresence(worker):
                while True:
                    worked = worker.run_once(args.run_id)
                    if args.once:
                        break
                    if not worked:
                        time.sleep(0.5)
    except InjectedCrash:
        # Controlled fault test: terminate without Python cleanup/checkpoint flushing.
        os._exit(86)
    except (SQLAlchemyError, psycopg.Error):
        raise SystemExit(
            "Database operation failed; inspect durable status before retrying"
        ) from None
    except WorkerAlreadyRunning:
        raise SystemExit("Another local RelayForge worker is active; ownership preserved") from None
    finally:
        database.close()


if __name__ == "__main__":
    main()

"""Single local scheduler; child execution always retains the real worker OS lock."""

import sys
import time
from uuid import uuid4

import psycopg
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from relayforge.config import ROOT, get_settings
from relayforge.db import Database
from relayforge.journal.service import Journal
from relayforge.journal.state import Status
from relayforge.models import Effect, EvaluationRun, EvaluationSuite, Run, RunEvent
from relayforge.runtime.presence import WorkerPresence
from relayforge.runtime.windows_job import WindowsJob
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning


class Supervisor:
    def __init__(self, database, settings, lock):
        self.database, self.sessions, self.settings, self.lock = (
            database,
            database.sessions,
            settings,
            lock,
        )
        self.owner = str(uuid4())
        self.current_run = self.current_suite = None
        self.activity = "idle"

    def choose(self):
        with self.sessions() as session:
            suite_id = session.scalar(
                select(EvaluationSuite.id)
                .join(EvaluationRun, EvaluationRun.suite_id == EvaluationSuite.id)
                .where(
                    EvaluationRun.status.in_(["QUEUED", "RUNNING"]),
                    EvaluationSuite.config["matrix_version"].as_integer() == 1,
                )
                .order_by(EvaluationSuite.created_at)
                .limit(1)
            )
            if suite_id:
                return "evaluation", suite_id
            run_id = session.scalar(
                select(Run.id)
                .where(
                    Run.evaluation_run_id.is_(None),
                    Run.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
                )
                .order_by(Run.created_at)
                .limit(1)
            )
            return ("worker", run_id) if run_id else None

    def finish_evaluation(self, identity, code):
        if not code:
            return
        try:
            with LocalWorkerLock(ROOT / ".local/evaluation.lock"), self.sessions.begin() as session:
                suite = session.get(EvaluationSuite, identity, with_for_update=True)
                suite.config = {
                    **suite.config,
                    "execution_error": {"category": "evaluation_process_exit", "code": code},
                }
                for evaluation in session.scalars(
                    select(EvaluationRun).where(EvaluationRun.suite_id == identity)
                ):
                    if evaluation.status in {"QUEUED", "RUNNING"}:
                        evaluation.status = "FAILED"
        except WorkerAlreadyRunning:
            return  # Another actual coordinator still owns it; never infer a failure.

    def finish_worker(self, identity, code):
        try:
            with LocalWorkerLock(ROOT / ".local/worker.lock"):
                with self.sessions.begin() as session:
                    run = session.get(Run, identity, with_for_update=True)
                    session.add(
                        RunEvent(
                            run_id=identity,
                            type="worker.process_exit",
                            data={"exit_code": code, "supervisor": self.owner},
                        )
                    )
                    exits = session.scalars(
                        select(RunEvent).where(
                            RunEvent.run_id == identity, RunEvent.type == "worker.process_exit"
                        )
                    ).all()
                    failures = sum(row.data["exit_code"] not in {0, 86} for row in exits)
                    if (
                        code not in {0, 86}
                        and failures >= 3
                        and run.status in {"RUNNING", "QUEUED"}
                    ):
                        run.status = "NEEDS_REVIEW"
                        session.add(
                            RunEvent(
                                run_id=identity,
                                type="run.status",
                                data={
                                    "status": "NEEDS_REVIEW",
                                    "reason": "repeated_worker_process_failure",
                                },
                            )
                        )
                if code not in {0, 86}:
                    with self.sessions() as session:
                        effects = session.scalars(
                            select(Effect).where(
                                Effect.run_id == identity, Effect.status == "IN_FLIGHT"
                            )
                        ).all()
                    for effect in effects:
                        Journal(self.sessions).outcome(
                            effect.effect_id,
                            Status.UNKNOWN,
                            None,
                            method="worker_exit_without_receipt",
                        )
        except WorkerAlreadyRunning:
            return  # A cooperating worker won the race; its active ownership stays intact.

    def run(self):
        with (
            WorkerPresence(self, self.settings.workspace_root.parent / "scheduler-status.json"),
            WindowsJob() as job,
        ):
            while True:
                try:
                    chosen = self.choose()
                    if not chosen:
                        self.activity = "idle"
                        time.sleep(0.4)
                        continue
                    kind, identity = chosen
                    try:
                        if kind == "evaluation":
                            with LocalWorkerLock(ROOT / ".local/evaluation.lock"):
                                pass
                        with LocalWorkerLock(ROOT / ".local/worker.lock"):
                            pass
                    except WorkerAlreadyRunning:
                        self.activity = "waiting_for_execution_ownership"
                        time.sleep(0.3)
                        continue
                    self.activity = kind
                    self.current_run = identity if kind == "worker" else None
                    self.current_suite = identity if kind == "evaluation" else None
                    arguments = (
                        [sys.executable, "-m", "relayforge.worker", "--once", "--run-id", identity]
                        if kind == "worker"
                        else [
                            sys.executable,
                            "-m",
                            "relayforge.evaluation.runner",
                            "--suite-id",
                            identity,
                        ]
                    )
                    child = job.spawn(
                        arguments,
                        ROOT / "backend",
                        ROOT / f".local/logs/{kind}-child.out.log",
                        ROOT / f".local/logs/{kind}-child.err.log",
                    )
                    try:
                        while child.poll() is None:
                            time.sleep(0.2)
                        code = child.poll()
                    finally:
                        child.close()
                    if kind == "worker":
                        self.finish_worker(identity, code)
                    else:
                        self.finish_evaluation(identity, code)
                    self.current_run = self.current_suite = None
                    time.sleep(0.2 if code in {0, 86} else 1)
                except (SQLAlchemyError, psycopg.Error):
                    self.activity = "database_unavailable"
                    time.sleep(2)


def main():
    settings = get_settings()
    database = Database(settings)
    try:
        if not database.ready():
            raise SystemExit("Database unavailable; local scheduler did not start")
        with LocalWorkerLock(ROOT / ".local/supervisor.lock") as lock:
            Supervisor(database, settings, lock).run()
    except WorkerAlreadyRunning:
        raise SystemExit("Another scheduler owns the local supervisor lock") from None
    except OSError:
        raise SystemExit(
            "Owned Windows child startup failed; no unowned process fallback"
        ) from None
    finally:
        database.close()


if __name__ == "__main__":
    main()

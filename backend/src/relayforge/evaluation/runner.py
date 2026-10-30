"""Matched local coding experiments with actual subprocess recovery and truth scoring."""

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version

from sqlalchemy import select

from relayforge.config import ROOT, Settings
from relayforge.db import Database
from relayforge.evaluation.metrics import unsafe_retries
from relayforge.evaluation.modes import executor_for
from relayforge.evaluation.report import write_reports
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, idempotency_key, logical_action_id
from relayforge.models import (
    Effect,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    Run,
    RunEvent,
)
from relayforge.providers.mock_coding import MockCodingProvider
from relayforge.providers.mock_external import KnownNoEffect, MockExternal
from relayforge.providers.templates import TEMPLATES
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import OutcomeUnknown
from relayforge.runtime.forks import Forks
from relayforge.runtime.graph import build_graph
from relayforge.runtime.presence import read_worker_presence
from relayforge.runtime.tasks import Tasks
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning
from relayforge.workspaces.docker_env import docker_cli, docker_environment
from relayforge.workspaces.manager import Workspaces
from relayforge.workspaces.sandbox import DockerSandbox

MODES = ["baseline", "journal", "full"]
FAULTS = {
    "none": ("none", 1, "normal execution"),
    "before_dispatch": ("before_dispatch", 2, "hard exit before provider dispatch"),
    "response_lost": ("response_lost", 2, "provider commits then response is lost"),
    "commit_without_receipt": (
        "after_external_before_receipt",
        2,
        "hard exit after external commit before receipt",
    ),
    "duplicate_delivery": (
        "none",
        1,
        "eight duplicate deliveries of the same confirmed logical action",
    ),
    "changed_arguments": (
        "none",
        1,
        "same logical action, changed payload; provider/runtime conflict observation",
    ),
    "no_idempotency_no_query": (
        "response_lost",
        2,
        "preauthorized non-idempotent operation loses response with no query support",
    ),
    "queryable_non_idempotent_loss": (
        "response_lost",
        2,
        "preauthorized non-idempotent operation loses response; query evidence is available",
    ),
    "fork_old_checkpoint": (
        "none",
        1,
        "Fork inspect checkpoint after parent publication; isolated snapshot and read-only history",
    ),
    "high_risk_unapproved": ("none", 1, "non-idempotent local mock tool without approval"),
    "sandbox_terminated": (
        "sandbox_terminated",
        1,
        "actual Docker container termination before tests complete",
    ),
    "worker_restart": (
        "after_dispatch_before_call",
        2,
        "hard exit after dispatch claim, fresh worker resumes checkpoint",
    ),
}


def sha(data: bytes):
    return hashlib.sha256(data).hexdigest()


def count_tests(output):
    matches = re.findall(r"Ran (\d+) tests? in", output)
    return int(matches[-1]) if matches else 0


class ExperimentRunner:
    def __init__(self, database, settings):
        self.database, self.sessions, self.settings = database, database.sessions, settings
        self.workspaces = Workspaces(settings.workspace_root)
        self.tasks = Tasks(self.sessions, self.workspaces, ROOT / "demo/sample_repo")
        self.truth = MockExternal(settings.mock_external_path)

    def environment(self):
        cli = docker_cli()
        inspected = subprocess.run(
            [cli, "image", "inspect", "relayforge-sandbox:local", "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            timeout=15,
            env=docker_environment(cli),
            check=False,
        )
        if inspected.returncode:
            raise RuntimeError(
                "Sandbox image/engine unavailable; no experiment measurements generated"
            )
        git = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        files = [
            *sorted((ROOT / "backend/src").rglob("*.py")),
            *sorted((ROOT / "demo").rglob("*.py")),
            ROOT / "backend/uv.lock",
            ROOT / "backend/pyproject.toml",
            ROOT / "sandbox/Dockerfile",
        ]
        manifest = {
            str(path.relative_to(ROOT)).replace("\\", "/"): sha(path.read_bytes()) for path in files
        }
        with self.database.engine.connect() as connection:
            from sqlalchemy import text

            postgres = connection.scalar(text("SELECT version()"))
        return {
            "os": platform.platform(),
            "machine": platform.machine(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
            "postgres": postgres,
            "sandbox_image": inspected.stdout.strip(),
            "versions": {
                name: version(name)
                for name in ["langgraph", "langgraph-checkpoint-postgres", "SQLAlchemy", "psycopg"]
            },
            "git_head": git.stdout.strip(),
            "working_tree_dirty": bool(dirty.stdout.strip()),
            "source_manifest": manifest,
            "manifest_sha256": sha(json.dumps(manifest, sort_keys=True).encode()),
            "provider": "mock",
        }

    @staticmethod
    def assert_source(manifest):
        for relative, digest in manifest.items():
            path = ROOT / relative
            if not path.is_file() or sha(path.read_bytes()) != digest:
                raise RuntimeError(
                    "measurement_source_changed; preserve this suite and start a new one"
                )

    def create_suite(self, templates, faults, repeats):
        if self.settings.model_provider != "mock":
            raise ValueError("Measurements require the explicit no-cost mock provider")
        if (
            not templates
            or not faults
            or not set(templates) <= set(TEMPLATES)
            or not set(faults) <= set(FAULTS)
            or not 1 <= repeats <= 5
        ):
            raise ValueError("Invalid bounded evaluation configuration")
        environment = self.environment()
        config = {
            "matrix_version": 1,
            "templates": templates,
            "faults": faults,
            "repeats": repeats,
            "modes": MODES,
            "mode_order": "rotate by paired case index, template ordinal and deterministic replicate",
            "timing_scope": "worker startup/execution/recovery/probes; excludes task setup and independent evaluator tests",
            "seed_semantics": "deterministic replicate identifier; no fabricated stochastic task variation",
            "environment": environment,
        }
        with self.sessions.begin() as session:
            suite = EvaluationSuite(name="Matched local coding fault matrix", config=config)
            session.add(suite)
            session.flush()
            suite_id = suite.id
            for mode in MODES:
                session.add(EvaluationRun(suite_id=suite_id, mode=mode))
            for repeat in range(repeats):
                for template in templates:
                    for fault in faults:
                        session.add(
                            EvaluationCase(
                                suite_id=suite_id, template=template, fault=fault, seed=repeat
                            )
                        )
        return suite_id

    def worker(self, run_id, attempt):
        if attempt:
            with self.sessions.begin() as session:
                run = session.get(Run, run_id, with_for_update=True)
                if run.status in {"NEEDS_REVIEW", "FAILED"}:
                    run.status = "QUEUED"
                    session.add(
                        RunEvent(
                            run_id=run_id,
                            type="evaluation.resume_requested",
                            data={"attempt": attempt + 1},
                        )
                    )
        started = time.perf_counter()
        deadline = started + 2
        while True:
            try:
                with LocalWorkerLock(ROOT / ".local/worker.lock"):
                    break
            except WorkerAlreadyRunning:
                if time.perf_counter() >= deadline:
                    raise RuntimeError("active_worker_ownership_unavailable") from None
                time.sleep(0.02)
        process = subprocess.run(
            [sys.executable, "-m", "relayforge.worker", "--once", "--run-id", run_id],
            cwd=ROOT / "backend",
            capture_output=True,
            timeout=90,
            check=False,
        )
        signal = read_worker_presence(self.settings)
        with self.sessions() as session:
            owner = session.get(Run, run_id).owner
        result = {
            "exit_code": process.returncode,
            "elapsed_s": time.perf_counter() - started,
            "owner": owner,
            "interpreter_pid": signal.get("process_id") if signal.get("owner") == owner else None,
        }
        if process.returncode not in {0, 86}:
            raise RuntimeError("worker_infrastructure_exit_" + str(process.returncode))
        return result

    def executor(self, run):
        return executor_for(
            self.sessions,
            self.workspaces,
            self.settings,
            run.mode,
            run.execution_config,
            run.evaluation_run_id,
        )

    def checkpoint(self, run_id):
        with self.sessions() as session:
            run = session.get(Run, run_id)
        with postgres_checkpointer(self.settings) as saver:
            return build_graph(self.executor(run), saver).get_state(
                {"configurable": {"thread_id": run_id}}
            )

    def probes(self, case, run_id):
        evidence = {"supported": True, "passed": None}
        with self.sessions() as session:
            run = session.get(Run, run_id)
        executor = self.executor(run)
        if case.fault == "duplicate_delivery":

            def deliver(_):
                try:
                    executor.execute(
                        run_id,
                        run.branch_id,
                        logical_action_id("publish"),
                        "mock.create",
                        {"subject": "sample-repair-result"},
                    )
                    return "receipt"
                except OutcomeUnknown:
                    return "claim_retained"

            with (
                LocalWorkerLock(ROOT / ".local/worker.lock"),
                ThreadPoolExecutor(max_workers=8) as pool,
            ):
                evidence["deliveries"] = list(pool.map(deliver, range(8)))
        elif case.fault == "changed_arguments":
            with LocalWorkerLock(ROOT / ".local/worker.lock"):
                try:
                    executor.execute(
                        run_id,
                        run.branch_id,
                        logical_action_id("publish"),
                        "mock.create",
                        {"subject": "explicit changed input probe"},
                    )
                    evidence["conflict"] = "not_refused"
                except (Conflict, KnownNoEffect) as exc:
                    evidence["conflict"] = type(exc).__name__
        elif case.fault == "fork_old_checkpoint":
            if run.mode != "full":
                return (
                    run_id,
                    {
                        "supported": False,
                        "passed": None,
                        "reason": "Fork isolation is unavailable in this ablation; parent coding is still measured",
                    },
                    [],
                )
            with (
                LocalWorkerLock(ROOT / ".local/worker.lock"),
                postgres_checkpointer(self.settings) as saver,
            ):
                forks = Forks(executor, saver)
                point = next(
                    row
                    for row in forks.history(run_id)
                    if row["stage"] == "inspect" and row["snapshot_id"]
                )
                child_id = forks.create(run_id, point["checkpoint_id"])
            with self.sessions() as session:
                child = session.get(Run, child_id)
            original = self.source(case.template).read_text(encoding="utf-8")
            evidence["restored_selected_bug"] = (
                self.workspaces.read(child.branch_id, "calculator.py") == original
            )
            evidence["independent_files"] = not self.workspaces.file(
                child.branch_id, "calculator.py"
            ).samefile(self.workspaces.file(run.branch_id, "calculator.py"))
            child_worker = self.worker(child_id, 0)
            evidence["parent_unchanged"] = self.workspaces.read(
                run.branch_id, "calculator.py"
            ) == MockCodingProvider.patch(case.template, original)
            evidence["parent_run_id"] = run_id
            return child_id, evidence, [child_worker]
        return run_id, evidence, []

    def source(self, template):
        return (
            ROOT
            / "demo"
            / ("sample_repo" if template == "fix_addition" else "templates/" + template)
            / "calculator.py"
        )

    def observe(self, case, run_id, primary, workers, probe, elapsed):
        with self.sessions() as session:
            runs = [session.get(Run, identity) for identity in dict.fromkeys([primary, run_id])]
            run = next(row for row in runs if row.id == run_id)
            effects = session.scalars(
                select(Effect).where(
                    Effect.run_id.in_([r.id for r in runs]),
                    Effect.tool_name.in_(["mock.create", "mock.notify"]),
                )
            ).all()
            events = session.scalars(
                select(RunEvent)
                .where(RunEvent.run_id.in_([r.id for r in runs]))
                .order_by(RunEvent.id)
            ).all()
        keys = {
            idempotency_key(row.branch_id, logical_action_id("publish")): row.id for row in runs
        }
        effect_keys = {effect.effect_id: effect.idempotency_key for effect in effects}
        run_keys = {
            row.id: key for key, identity in keys.items() for row in runs if row.id == identity
        }
        proofs = {key: [] for key in keys}
        for event in events:
            key = (
                effect_keys.get(event.effect_id) if event.effect_id else run_keys.get(event.run_id)
            )
            if (
                key in proofs
                and event.data.get("outcome") == "not_occurred"
                and event.data.get("monotonic_ns")
                and (
                    event.effect_id
                    or (
                        event.type == "baseline.protocol_rejection"
                        and event.data.get("action_id") == logical_action_id("publish")
                    )
                )
            ):
                proofs[key].append(event.data["monotonic_ns"])
        histories = {key: self.truth.request_history(key) for key in keys}
        retry_counts = [unsafe_retries(history, proofs[key]) for key, history in histories.items()]
        actual = sum(self.truth.truth_count(key) for key in keys)
        requests = sum(len(history) for history in histories.values())
        checkpoint_snapshot = self.checkpoint(run_id)
        checkpoint = checkpoint_snapshot.values
        tested = (
            self.executor(run).workspace.result(checkpoint["test_receipt"])
            if checkpoint.get("test_receipt")
            else None
        )
        expected_source = MockCodingProvider.patch(
            case.template, self.source(case.template).read_text(encoding="utf-8")
        )
        source_hash = sha(self.workspaces.read(run.branch_id, "calculator.py").encode())
        trusted = self.source(case.template).parent / "test_calculator.py"
        expected_tests = sha(trusted.read_bytes())
        actual_tests = sha(self.workspaces.file(run.branch_id, "test_calculator.py").read_bytes())
        independent = DockerSandbox(self.workspaces).run_tests(run.branch_id)
        independent_count = count_tests(independent.output)
        if independent_count < 1:
            raise RuntimeError("independent_test_infrastructure_unavailable")
        source_after = sha(self.workspaces.read(run.branch_id, "calculator.py").encode())
        independent_accepted = bool(
            independent.passed
            and source_hash == source_after == sha(expected_source.encode())
            and expected_tests == actual_tests
        )
        reported_test_passed = bool(
            tested
            and tested["passed"]
            and tested["exit_code"] == 0
            and tested["source_hash"] == source_hash
        )
        coding_run = next(row for row in runs if row.id == primary)
        coding_checkpoint = checkpoint if primary == run_id else self.checkpoint(primary).values
        coding_tested = (
            self.executor(coding_run).workspace.result(coding_checkpoint["test_receipt"])
            if coding_checkpoint.get("test_receipt")
            else None
        )
        coding_independent = (
            independent
            if primary == run_id
            else DockerSandbox(self.workspaces).run_tests(coding_run.branch_id)
        )
        if count_tests(coding_independent.output) < 1:
            raise RuntimeError("primary_test_infrastructure_unavailable")
        coding_source = sha(self.workspaces.read(coding_run.branch_id, "calculator.py").encode())
        coding_tests = sha(
            self.workspaces.file(coding_run.branch_id, "test_calculator.py").read_bytes()
        )
        coding_accepted = bool(
            coding_independent.passed
            and coding_source == sha(expected_source.encode())
            and coding_tests == expected_tests
        )
        coding_reported = bool(
            coding_tested
            and coding_tested["passed"]
            and coding_tested["source_hash"] == coding_source
        )
        asserted = "occurred" if checkpoint.get("publish_receipt") else None
        if not asserted and effects and all(row.status == "FAILED" for row in effects):
            asserted = "not_occurred"
        if not asserted and run.mode == "baseline":
            relevant = [
                row
                for row in events
                if row.type in {"baseline.protocol_rejection", "baseline.tool_dispatch"}
                and row.data.get("action_id") == logical_action_id("publish")
            ]
            if relevant and relevant[-1].type == "baseline.protocol_rejection" and requests == 1:
                asserted = "not_occurred"
        recovery_eligible = case.fault in {
            "before_dispatch",
            "response_lost",
            "commit_without_receipt",
            "worker_restart",
            "sandbox_terminated",
            "queryable_non_idempotent_loss",
        }
        if case.fault == "sandbox_terminated":
            recovery_correct = bool(
                run.status == "FAILED"
                and tested
                and tested["fault_injected"]
                and not tested["passed"]
                and actual == 0
            )
        else:
            recovery_correct = bool(
                run.status == "SUCCEEDED"
                and independent_accepted
                and reported_test_passed
                and actual == 1
            )
        if case.fault == "fork_old_checkpoint" and probe["supported"]:
            probe["passed"] = bool(
                probe["restored_selected_bug"]
                and probe["independent_files"]
                and probe["parent_unchanged"]
                and actual == 1
                and self.truth.truth_count(
                    idempotency_key(run.branch_id, logical_action_id("publish"))
                )
                == 0
                and run.status == "SUCCEEDED"
            )
        elif case.fault == "duplicate_delivery":
            probe["passed"] = actual == 1 and len(probe["deliveries"]) == 8
        elif case.fault == "changed_arguments":
            probe["passed"] = actual == 1 and probe["conflict"] in {"Conflict", "KnownNoEffect"}
        elif case.fault == "high_risk_unapproved":
            probe["passed"] = actual == requests == 0 and run.status == "NEEDS_REVIEW"
            probe["permission_mechanism"] = (
                "common_fixture_guard" if run.mode == "baseline" else "branch_payload_approval"
            )
        return {
            "phase": "COMPLETED",
            "case_schema_version": 2,
            "mode": run.mode,
            "case_id": case.id,
            "template": case.template,
            "fault": case.fault,
            "seed": case.seed,
            "repeat": case.seed,
            "run_id": run_id,
            "primary_run_id": primary,
            "coding_run_id": primary,
            "coding_run_status": coding_run.status,
            "coding_independent_acceptance": coding_accepted,
            "coding_reported_test_passed": coding_reported,
            "coding_independent_test_count": count_tests(coding_independent.output),
            "coding_independent_test_output": coding_independent.output,
            "branch_id": run.branch_id,
            "run_status": run.status,
            "reported_test_exit_code": run.test_exit_code,
            "reported_test_passed": reported_test_passed,
            "independent_acceptance": independent_accepted,
            "independent_test_exit_code": independent.exit_code,
            "independent_test_count": independent_count,
            "independent_test_output": independent.output,
            "source_hash": source_hash,
            "expected_source_hash": sha(expected_source.encode()),
            "trusted_test_hash": expected_tests,
            "workspace_test_hash": actual_tests,
            "actual_external_effects": actual,
            "external_requests": requests,
            "request_histories": histories,
            "absence_proof_clocks": proofs,
            "unsafe_retry_count": sum(retry_counts)
            if all(v is not None for v in retry_counts)
            else None,
            "asserted_external_outcome": asserted,
            "effect_states": [
                {"id": row.effect_id, "status": row.status, "method": row.reconciliation_method}
                for row in effects
            ],
            "recovery_eligible": recovery_eligible,
            "recovery_correct": recovery_correct if recovery_eligible else None,
            "safe_unresolved": run.status == "NEEDS_REVIEW"
            and case.fault != "high_risk_unapproved"
            and all(v == 0 for v in retry_counts),
            "probe_supported": probe["supported"],
            "probe": probe,
            "workers": workers,
            "workflow_elapsed_s": elapsed,
            "fault_boundary": FAULTS[case.fault][2],
            "infrastructure_error": None,
            "scope": "matrix",
            "thread_id": run_id,
            "checkpoint_id": checkpoint_snapshot.config.get("configurable", {}).get(
                "checkpoint_id"
            ),
            "workspace_snapshot_id": checkpoint.get("snapshot_id"),
            "input_source_hash": sha(
                self.source(case.template).read_text(encoding="utf-8").encode()
            ),
            "execution_config": run.execution_config,
            "fault_source_event_ids": [row.id for row in events if row.type == "fault.injected"],
            "recovery_trace": [
                {
                    "source_event_id": row.id,
                    "run_id": row.run_id,
                    "type": row.type,
                    "data": row.data,
                }
                for row in events
                if row.type in {"effect.outcome", "worker.claimed", "evaluation.resume_requested"}
            ],
        }

    def evaluate_case(self, case, evaluation):
        injected, attempts, _ = FAULTS[case.fault]
        config = {}
        if case.fault in {"no_idempotency_no_query", "queryable_non_idempotent_loss"}:
            config = {
                "publish_tool": "mock.notify",
                "supports_idempotency": False,
                "supports_query": case.fault == "queryable_non_idempotent_loss",
                "preauthorized": True,
            }
        elif case.fault == "high_risk_unapproved":
            config = {"publish_tool": "mock.notify", "preauthorized": False}
        _, primary = self.tasks.create(
            "Evaluation: " + case.template + "/" + case.fault,
            case.template,
            injected,
            mode=evaluation.mode,
            execution_config=config,
            evaluation_run_id=evaluation.id,
        )
        with self.sessions.begin() as session:
            row = EvaluationResult(
                evaluation_run_id=evaluation.id,
                case_id=case.id,
                run_id=primary,
                observed={
                    "phase": "RUNNING",
                    "scope": "matrix",
                    "mode": evaluation.mode,
                    "case_id": case.id,
                    "template": case.template,
                    "fault": case.fault,
                    "seed": case.seed,
                    "run_id": primary,
                },
            )
            session.add(row)
            session.flush()
            result_id = row.id
        started = time.perf_counter()
        workers = []
        try:
            for attempt in range(attempts):
                workers.append(self.worker(primary, attempt))
            run_id, probe, child_workers = self.probes(case, primary)
            workers += child_workers
            elapsed = time.perf_counter() - started
            observed = self.observe(case, run_id, primary, workers, probe, elapsed)
            if (
                injected
                in {
                    "before_dispatch",
                    "after_external_before_receipt",
                    "after_dispatch_before_call",
                }
                and workers[0]["exit_code"] != 86
            ):
                raise RuntimeError("expected_hard_process_exit_not_observed")
            observed["case_elapsed_s"] = time.perf_counter() - started
        except Exception as exc:  # noqa: BLE001 - persist every case failure; suite exits nonzero.
            run_id = primary
            with self.sessions() as session:
                status = session.get(Run, primary).status
            observed = {
                "phase": "COMPLETED",
                "scope": "matrix",
                "mode": evaluation.mode,
                "case_id": case.id,
                "template": case.template,
                "fault": case.fault,
                "seed": case.seed,
                "run_id": primary,
                "run_status": status,
                "workers": workers,
                "infrastructure_error": type(exc).__name__,
                "error_category": str(exc)
                if isinstance(exc, RuntimeError)
                else "controlled_evaluation_error",
            }
        with self.sessions.begin() as session:
            row = session.get(EvaluationResult, result_id)
            row.run_id, row.observed = run_id, observed
        return observed

    def interrupt_measurement(self, result_id):
        # A timestamp or coordinator restart cannot prove a worker has stopped.
        with LocalWorkerLock(ROOT / ".local/worker.lock"):
            with self.sessions() as session:
                row = session.get(EvaluationResult, result_id)
                if row.observed.get("phase") == "COMPLETED":
                    return row
                run = session.get(Run, row.run_id)
                if run.evaluation_run_id != row.evaluation_run_id:
                    raise RuntimeError("Interrupted measurement identity does not match its run")
                pending = session.scalars(
                    select(Effect.effect_id).where(
                        Effect.run_id == run.id, Effect.status == "IN_FLIGHT"
                    )
                ).all()
            # Separate outcome transactions retain the normal Journal audit rules.
            # If persistence fails, the observation stays unfinished for another audit.
            for effect_id in pending:
                Journal(self.sessions).outcome(
                    effect_id,
                    "UNKNOWN",
                    None,
                    method="interrupted_evaluation_without_completion_receipt",
                )
            with self.sessions.begin() as session:
                row = session.get(EvaluationResult, result_id, with_for_update=True)
                run = session.get(Run, row.run_id, with_for_update=True)
                if run.status in {"PREPARING", "QUEUED", "RUNNING"}:
                    run.status = "NEEDS_REVIEW"
                elif run.status == "CANCEL_REQUESTED":
                    run.status = "CANCELLED"
                run.lease_until = None
                session.add(
                    RunEvent(
                        run_id=run.id,
                        type="evaluation.measurement_interrupted",
                        data={
                            "status": run.status,
                            "ownership_evidence": "exclusive_worker_os_lock",
                            "measurement": "infrastructure_failure_not_retimed",
                        },
                    )
                )
                row.observed = {
                    **row.observed,
                    "phase": "COMPLETED",
                    "infrastructure_error": "interrupted_runner_measurement",
                    "run_status": run.status,
                }
                session.flush()
                session.expunge(row)
                return row

    def run_suite(self, suite_id):
        with self.sessions() as session:
            suite = session.get(EvaluationSuite, suite_id)
            if not suite or suite.config.get("matrix_version") != 1:
                raise ValueError("Not a supported matrix suite")
            cases = session.scalars(
                select(EvaluationCase)
                .where(EvaluationCase.suite_id == suite_id)
                .order_by(EvaluationCase.seed, EvaluationCase.template, EvaluationCase.fault)
            ).all()
            evaluations = {
                row.mode: row
                for row in session.scalars(
                    select(EvaluationRun).where(EvaluationRun.suite_id == suite_id)
                )
            }
            metadata = {"suite_id": suite_id, "configuration": suite.config}
        destination = ROOT / "artifacts/evaluations" / suite_id
        for index, case in enumerate(cases):
            offset = (index + suite.config["templates"].index(case.template) + case.seed) % 3
            order = MODES[offset:] + MODES[:offset]
            for mode in order:
                evaluation = evaluations[mode]
                with self.sessions() as session:
                    previous = session.scalar(
                        select(EvaluationResult).where(
                            EvaluationResult.evaluation_run_id == evaluation.id,
                            EvaluationResult.case_id == case.id,
                        )
                    )
                if previous and previous.observed.get("phase") != "COMPLETED":
                    previous = self.interrupt_measurement(previous.id)
                complete = previous is not None
                with self.sessions.begin() as session:
                    session.get(EvaluationRun, evaluation.id).status = "RUNNING"
                if not complete:
                    self.assert_source(suite.config["environment"]["source_manifest"])
                    observed = self.evaluate_case(case, evaluation)
                    try:
                        self.assert_source(suite.config["environment"]["source_manifest"])
                    except RuntimeError:
                        with self.sessions.begin() as session:
                            affected = session.scalar(
                                select(EvaluationResult).where(
                                    EvaluationResult.evaluation_run_id == evaluation.id,
                                    EvaluationResult.case_id == case.id,
                                )
                            )
                            affected.observed = {
                                **affected.observed,
                                "infrastructure_error": "source_changed_during_measurement",
                            }
                        raise
                    print(
                        json.dumps(
                            {
                                "case": index + 1,
                                "paired_cases": len(cases),
                                "mode": mode,
                                "template": case.template,
                                "fault": case.fault,
                                "status": observed["run_status"],
                                "infrastructure_error": observed.get("infrastructure_error"),
                            }
                        ),
                        flush=True,
                    )
                with self.sessions() as session:
                    rows = session.scalars(
                        select(EvaluationResult)
                        .join(EvaluationRun, EvaluationRun.id == EvaluationResult.evaluation_run_id)
                        .where(EvaluationRun.suite_id == suite_id)
                        .order_by(EvaluationResult.id)
                    ).all()
                    records = [
                        row.observed for row in rows if row.observed.get("phase") == "COMPLETED"
                    ]
                write_reports(destination, records, metadata)
        failed = False
        with self.sessions.begin() as session:
            for evaluation in evaluations.values():
                results = session.scalars(
                    select(EvaluationResult).where(
                        EvaluationResult.evaluation_run_id == evaluation.id
                    )
                ).all()
                errors = any(row.observed.get("infrastructure_error") for row in results)
                session.get(EvaluationRun, evaluation.id).status = (
                    "FAILED" if errors else "COMPLETED"
                )
                failed |= errors
        print(
            json.dumps(
                {"suite_id": suite_id, "output": str(destination), "infrastructure_failed": failed}
            ),
            flush=True,
        )
        return not failed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-id")
    parser.add_argument("--templates", nargs="+", choices=list(TEMPLATES))
    parser.add_argument("--faults", nargs="+", choices=list(FAULTS))
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    settings = Settings()
    if settings.model_provider != "mock":
        raise SystemExit("Evaluation requires MODEL_PROVIDER=mock; no remote requests made")
    database = Database(settings)
    try:
        if not database.ready():
            raise SystemExit("Database unavailable; no measurements generated")
        with LocalWorkerLock(ROOT / ".local/evaluation.lock"):
            # Probe exclusivity then release before child workers; never transfer on a lease.
            with LocalWorkerLock(ROOT / ".local/worker.lock"):
                pass
            runner = ExperimentRunner(database, settings)
            suite_id = args.suite_id or runner.create_suite(
                args.templates or list(TEMPLATES), args.faults or list(FAULTS), args.repeats
            )
            print(json.dumps({"suite_id": suite_id, "state": "starting"}), flush=True)
            if not runner.run_suite(suite_id):
                raise SystemExit("Evaluation retained infrastructure failures; inspect raw report")
    finally:
        database.close()


if __name__ == "__main__":
    main()

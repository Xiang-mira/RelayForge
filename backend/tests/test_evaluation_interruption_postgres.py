from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_evaluation_modes_postgres import setup_mode

from relayforge.config import Settings
from relayforge.evaluation.runner import ExperimentRunner
from relayforge.models import Effect, EvaluationCase, EvaluationResult, EvaluationRun, Run, RunEvent
from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["baseline", "journal", "full"])
def test_interrupted_measurement_requires_stopped_owner_and_preserves_uncertainty(
    seeded, tmp_path, monkeypatch, mode
):
    import relayforge.evaluation.runner as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    executor, _ = setup_mode(seeded, tmp_path, mode)
    sessions, (run_id, branch_id) = seeded[1], seeded[2]
    effect_id = None
    if mode != "baseline":
        effect = executor.journal.prepare(
            run_id,
            branch_id,
            "interrupted",
            "mock.create",
            "IDEMPOTENT_WRITE",
            {"subject": "unit fixture"},
            "allow",
        )
        effect_id = effect.effect_id
        assert executor.journal.dispatch(effect_id, authorized=True)
    with sessions.begin() as session:
        run = session.get(Run, run_id)
        run.status = "RUNNING"
        evaluation = session.get(EvaluationRun, run.evaluation_run_id)
        case = EvaluationCase(
            suite_id=evaluation.suite_id, template="fix_addition", fault="none", seed=0
        )
        session.add(case)
        session.flush()
        result = EvaluationResult(
            evaluation_run_id=evaluation.id,
            case_id=case.id,
            run_id=run_id,
            observed={"phase": "RUNNING", "mode": mode},
        )
        session.add(result)
        session.flush()
        result_id = result.id
    runner = ExperimentRunner(
        SimpleNamespace(sessions=sessions),
        Settings(
            workspace_root=tmp_path / "workspaces", mock_external_path=tmp_path / "truth.sqlite3"
        ),
    )
    with LocalWorkerLock(tmp_path / ".local/worker.lock"):
        with pytest.raises(WorkerAlreadyRunning):
            runner.interrupt_measurement(result_id)
        with sessions() as session:
            assert session.get(Run, run_id).status == "RUNNING"
            assert session.get(EvaluationResult, result_id).observed["phase"] == "RUNNING"
            if effect_id:
                assert session.get(Effect, effect_id).status == "IN_FLIGHT"
    observed = runner.interrupt_measurement(result_id).observed
    assert observed["run_status"] == "NEEDS_REVIEW"
    assert observed["infrastructure_error"] == "interrupted_runner_measurement"
    assert runner.interrupt_measurement(result_id).observed == observed
    with sessions() as session:
        assert session.get(Run, run_id).status == "NEEDS_REVIEW"
        events = session.scalars(
            select(RunEvent).where(RunEvent.type == "evaluation.measurement_interrupted")
        ).all()
        assert len(events) == 1
        if effect_id:
            effect = session.get(Effect, effect_id)
            assert effect.status == "UNKNOWN"
            assert effect.reconciliation_result["outcome"] == "indeterminate"

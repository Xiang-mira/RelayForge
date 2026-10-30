from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from relayforge.config import Settings
from relayforge.evaluation.modes import executor_for
from relayforge.journal.state import Conflict, Status, idempotency_key
from relayforge.models import Effect, EvaluationRun, EvaluationSuite, Run
from relayforge.providers.mock_external import KnownNoEffect
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ApprovalRequired, OutcomeUnknown
from relayforge.runtime.graph import build_graph
from relayforge.workspaces.manager import Workspaces

pytestmark = pytest.mark.postgres
MODES = ["baseline", "journal", "full"]


def setup_mode(seeded, tmp_path, mode, **config):
    engine, sessions, (run_id, _) = seeded
    with sessions.begin() as session:
        suite = EvaluationSuite(name="Mode acceptance", config={})
        session.add(suite)
        session.flush()
        evaluation = EvaluationRun(suite_id=suite.id, mode=mode)
        session.add(evaluation)
        session.flush()
        run = session.get(Run, run_id)
        run.mode, run.execution_config, run.evaluation_run_id = mode, config, evaluation.id
        evaluation_id = evaluation.id
    settings = Settings(
        workspace_root=tmp_path / "workspaces", mock_external_path=tmp_path / "truth.sqlite3"
    )
    executor = executor_for(
        sessions, Workspaces(settings.workspace_root), settings, mode, config, evaluation_id
    )
    return executor, engine


@pytest.mark.docker
@pytest.mark.parametrize("mode", MODES)
def test_same_real_coding_graph_and_tests_across_three_modes(seeded, tmp_path, mode):
    executor, engine = setup_mode(seeded, tmp_path, mode)
    run_id, branch_id = seeded[2]
    executor.workspace.workspaces.create(
        branch_id, Path(__file__).resolve().parents[2] / "demo/sample_repo"
    )
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        result = build_graph(executor, saver).invoke(
            {"run_id": run_id, "branch_id": branch_id, "template": "fix_addition", "fault": "none"},
            {"configurable": {"thread_id": run_id}},
        )
    assert result["result"] == "SUCCEEDED"
    assert executor.workspace.result(result["test_receipt"])["exit_code"] == 0
    with seeded[1]() as session:
        count = session.scalar(select(func.count()).select_from(Effect))
        assert count == (0 if mode == "baseline" else 5)


@pytest.mark.parametrize("mode", MODES)
def test_response_loss_keeps_a_safe_baseline_and_distinct_query_ablation(seeded, tmp_path, mode):
    executor, _ = setup_mode(seeded, tmp_path, mode)
    ids = seeded[2]
    key = idempotency_key(ids[1], "publish")
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, "publish", "mock.create", {"subject": "same input"}, "response_lost")
    if mode == "baseline":
        assert executor.execute(*ids, "publish", "mock.create", {"subject": "same input"})
    else:
        with seeded[1]() as session:
            effect_id = session.scalar(select(Effect)).effect_id
        recovered = executor.reconcile(effect_id, owner_stopped=True)
        assert recovered.status == (Status.CONFIRMED if mode == "full" else Status.UNKNOWN)
        assert executor.execute(*ids, "publish", "mock.create", {"subject": "same input"})
    assert executor.external.truth_count(key) == 1
    assert executor.external.request_count(key) == (1 if mode == "full" else 2)


@pytest.mark.parametrize("mode", MODES)
def test_all_modes_preserve_unqueryable_non_idempotent_uncertainty(seeded, tmp_path, mode):
    executor, _ = setup_mode(
        seeded,
        tmp_path,
        mode,
        supports_idempotency=False,
        supports_query=False,
        publish_tool="mock.notify",
        preauthorized=True,
    )
    ids = seeded[2]
    key = idempotency_key(ids[1], "notify")
    with pytest.raises(OutcomeUnknown):
        executor.execute(
            *ids, "notify", "mock.notify", {"subject": "preauthorized fixture"}, "response_lost"
        )
    for _ in range(3):
        with pytest.raises(OutcomeUnknown):
            executor.execute(*ids, "notify", "mock.notify", {"subject": "preauthorized fixture"})
    if mode != "baseline":
        with seeded[1]() as session:
            identity = session.scalar(select(Effect)).effect_id
        assert executor.reconcile(identity, owner_stopped=True).status == Status.UNKNOWN
    assert executor.external.truth_count(key) == executor.external.request_count(key) == 1


@pytest.mark.parametrize("mode", MODES)
def test_only_query_enabled_mode_resolves_non_idempotent_loss_with_available_evidence(
    seeded, tmp_path, mode
):
    executor, _ = setup_mode(
        seeded,
        tmp_path,
        mode,
        supports_idempotency=False,
        supports_query=True,
        publish_tool="mock.notify",
        preauthorized=True,
    )
    ids = seeded[2]
    with pytest.raises(OutcomeUnknown):
        executor.execute(
            *ids, "notify", "mock.notify", {"subject": "preauthorized fixture"}, "response_lost"
        )
    if mode == "baseline":
        with pytest.raises(OutcomeUnknown):
            executor.execute(*ids, "notify", "mock.notify", {"subject": "preauthorized fixture"})
    else:
        with seeded[1]() as session:
            identity = session.scalar(select(Effect)).effect_id
        assert executor.reconcile(identity, owner_stopped=True).status == (
            Status.CONFIRMED if mode == "full" else Status.UNKNOWN
        )
    key = idempotency_key(ids[1], "notify")
    assert executor.external.truth_count(key) == executor.external.request_count(key) == 1


@pytest.mark.parametrize("mode", MODES)
def test_all_modes_reject_unapproved_risk_and_native_duplicate_calls_are_not_poisoned(
    seeded, tmp_path, mode
):
    executor, _ = setup_mode(seeded, tmp_path, mode)
    ids = seeded[2]
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "notify", "mock.notify", {"subject": "not approved"})
    assert executor.external.request_count(idempotency_key(ids[1], "notify")) == 0

    def deliver(_):
        try:
            return executor.execute(*ids, "publish", "mock.create", {"subject": "same input"})
        except OutcomeUnknown:
            return None  # A real concurrent claim is never stolen.

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(deliver, range(16)))
    key = idempotency_key(ids[1], "publish")
    assert executor.external.truth_count(key) == 1
    assert executor.external.request_count(key) == (16 if mode == "baseline" else 1)
    with pytest.raises(KnownNoEffect if mode == "baseline" else Conflict):
        executor.execute(*ids, "publish", "mock.create", {"subject": "changed input"})
    assert executor.external.truth_count(key) == 1

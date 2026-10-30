from unittest.mock import Mock

import pytest
from sqlalchemy import select
from test_forks_postgres import setup_source

from relayforge.config import Settings
from relayforge.db import Database
from relayforge.journal.state import idempotency_key, logical_action_id, payload_hash
from relayforge.models import Effect, Run, RunEvent
from relayforge.providers.mock_coding import MockCodingProvider
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ApprovalRequired, OutcomeUnknown
from relayforge.runtime.graph import build_graph
from relayforge.runtime.publication import PublicationRejected
from relayforge.runtime.worker_lock import LocalWorkerLock
from relayforge.worker import Worker

pytestmark = [pytest.mark.postgres, pytest.mark.docker]


def ready(seeded, tmp_path):
    executor, state, schema = setup_source(seeded, tmp_path)
    return executor, state, schema, {"configurable": {"thread_id": state["run_id"]}}


def assert_no_publication(executor, state, spy):
    spy.assert_not_called()
    key = idempotency_key(state["branch_id"], logical_action_id("publish"))
    assert executor.external.request_count(key) == executor.external.truth_count(key) == 0
    with executor.journal.sessions() as session:
        assert session.get(Run, state["run_id"]).status == "NEEDS_REVIEW"
        assert session.scalar(
            select(RunEvent).where(
                RunEvent.run_id == state["run_id"], RunEvent.type == "publication.rejected"
            )
        )


@pytest.mark.parametrize("change", ["source", "tests", "dispatch_window"])
def test_changed_tested_inputs_block_actual_provider_on_resume(
    seeded, tmp_path, monkeypatch, change
):
    executor, state, schema, config = ready(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        graph = build_graph(executor, saver)
        graph.invoke(state, config, interrupt_after=["test"])
        spy = Mock(wraps=executor.external.execute)
        monkeypatch.setattr(executor.external, "execute", spy)

        def mutate():
            path = executor.workspace.workspaces.file(
                state["branch_id"], "test_calculator.py" if change == "tests" else "calculator.py"
            )
            path.write_bytes(path.read_bytes() + b"\n# Changed after tests\n")

        if change == "dispatch_window":
            dispatch = executor.journal.dispatch

            def change_after_claim(effect_id, authorized):
                claimed = dispatch(effect_id, authorized)
                if executor.journal.get(effect_id).logical_action_id == logical_action_id(
                    "publish"
                ):
                    mutate()
                return claimed

            monkeypatch.setattr(executor.journal, "dispatch", change_after_claim)
        else:
            mutate()
        with pytest.raises(PublicationRejected, match="workspace_changed"):
            graph.invoke(None, config)
    assert_no_publication(executor, state, spy)


def test_failed_tests_cannot_be_bypassed_by_direct_external_tool(seeded, tmp_path, monkeypatch):
    executor, state, schema, config = ready(seeded, tmp_path)

    class WrongPatch(MockCodingProvider):
        @staticmethod
        def patch(template, source):
            return source

    with postgres_checkpointer(Settings(), schema=schema) as saver:
        final = build_graph(executor, saver, WrongPatch()).invoke(state, config)
    assert final["result"] == "FAILED"
    assert executor.workspace.result(final["test_receipt"])["exit_code"] != 0
    spy = Mock(wraps=executor.external.execute)
    monkeypatch.setattr(executor.external, "execute", spy)
    with pytest.raises(PublicationRejected, match="test_not_completed_and_passed"):
        executor.execute(
            state["run_id"],
            state["branch_id"],
            "alternative-action",
            "mock.create",
            {"subject": "bypass fixed graph"},
        )
    assert_no_publication(executor, state, spy)


def test_unknown_and_missing_approval_cannot_dispatch_publication(seeded, tmp_path, monkeypatch):
    executor, state, schema, config = ready(seeded, tmp_path)
    ids = state["run_id"], state["branch_id"]
    action = logical_action_id("publish")
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        build_graph(executor, saver).invoke(state, config, interrupt_after=["test"])
    spy = Mock(wraps=executor.external.execute)
    monkeypatch.setattr(executor.external, "execute", spy)
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, action, "mock.notify", {"subject": "protected"})
    spy.assert_not_called()
    key = idempotency_key(ids[1], action)
    assert executor.external.request_count(key) == 0
    with executor.journal.sessions() as session:
        pending = session.scalar(select(Effect).where(Effect.logical_action_id == action))
    executor.approvals.decide(pending.effect_id, "APPROVED")
    # A real non-idempotent request commits and loses its response. Further calls,
    # including same-key native-style attempts or alternate IDs, cannot bypass UNKNOWN.
    with pytest.raises(OutcomeUnknown):
        executor.execute(*ids, action, "mock.notify", {"subject": "protected"}, "response_lost")
    assert executor.external.request_count(key) == executor.external.truth_count(key) == 1
    spy.reset_mock()
    with pytest.raises(PublicationRejected, match="unresolved_effect"):
        executor.execute(*ids, "new-write", "mock.create", {"subject": "must wait"})
    with pytest.raises(PublicationRejected, match="unresolved_effect"):
        executor.execute(*ids, action, "mock.notify", {"subject": "protected"})
    with pytest.raises(PublicationRejected, match="dispatch_not_authorized"):
        executor.call_external(pending, {"subject": "protected"}, "none")
    spy.assert_not_called()
    assert executor.external.request_count(key) == 1
    executor.reconcile(pending.effect_id, owner_stopped=True)
    assert executor.execute(*ids, action, "mock.notify", {"subject": "protected"})
    spy.assert_not_called()  # Confirmed recovery reuses the receipt.


@pytest.mark.parametrize("invalid", ["missing", "legacy", "wrong_task"])
def test_missing_legacy_or_wrong_task_evidence_is_not_publication_authority(
    seeded, tmp_path, monkeypatch, invalid
):
    executor, state, schema, config = ready(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        partial = build_graph(executor, saver).invoke(state, config, interrupt_after=["test"])
    key = partial["test_receipt"]["external_id"]
    import json

    path = executor.workspace._path(key)
    record = json.loads(path.read_text(encoding="utf-8"))
    if invalid == "missing":
        path.rename(path.with_suffix(".audit-missing"))
    elif invalid == "legacy":
        del record["result"]["acceptance"]
        executor.workspace._save(key, record)
    else:
        record["result"]["acceptance"]["task_id"] = "wrong-task"
        executor.workspace._save(key, record)
    spy = Mock(wraps=executor.external.execute)
    monkeypatch.setattr(executor.external, "execute", spy)
    with pytest.raises(PublicationRejected):
        executor.execute(
            state["run_id"],
            state["branch_id"],
            logical_action_id("publish"),
            "mock.create",
            {"subject": "sample-repair-result"},
        )
    assert_no_publication(executor, state, spy)


def test_completed_run_replay_rechecks_tests_and_never_resubmits(seeded, tmp_path, monkeypatch):
    executor, state, schema, config = ready(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        final = build_graph(executor, saver).invoke(state, config)
    proof = executor.publications.require(state["run_id"], state["branch_id"])
    tested = executor.workspace.result(final["test_receipt"])
    evidence = tested["acceptance"]
    assert evidence["run_id"] == state["run_id"] and evidence["test_run_id"]
    assert {"calculator.py", "test_calculator.py"} <= evidence["manifest"].keys()
    with executor.journal.sessions() as session:
        effect = session.scalar(select(Effect).where(Effect.tool_name == "mock.create"))
        assert effect.payload_hash == payload_hash(
            {
                "subject": "sample-repair-result",
                "_tested_version": proof["version"],
            }
        )
    key = idempotency_key(state["branch_id"], logical_action_id("publish"))
    assert executor.external.request_count(key) == executor.external.truth_count(key) == 1
    path = executor.workspace.workspaces.file(state["branch_id"], "test_calculator.py")
    path.write_bytes(path.read_bytes() + b"\n# Changed after SUCCEEDED\n")
    with executor.journal.sessions.begin() as session:
        session.get(Run, state["run_id"]).status = "QUEUED"
    engine = seeded[0]
    settings = Settings(
        database_url=engine.url.update_query_dict(
            {"options": "-csearch_path=" + schema}
        ).render_as_string(hide_password=False),
        workspace_root=executor.workspace.workspaces.root,
        mock_external_path=executor.external.path,
    )
    database = Database(settings)
    from relayforge.providers.mock_external import MockExternal

    spy = Mock(side_effect=AssertionError("Completed replay must not resubmit"))
    monkeypatch.setattr(MockExternal, "execute", spy)
    try:
        with LocalWorkerLock(tmp_path / "replay-worker.lock") as lock:
            assert Worker(database, settings, lock).run_once(state["run_id"])
    finally:
        database.close()
    spy.assert_not_called()
    with executor.journal.sessions() as session:
        assert session.get(Run, state["run_id"]).status == "NEEDS_REVIEW"
    assert executor.external.request_count(key) == executor.external.truth_count(key) == 1


def test_protected_receipt_recovery_and_post_test_fork_reuse_bound_version(seeded, tmp_path):
    from relayforge.providers.mock_external import InjectedCrash
    from relayforge.runtime.forks import Forks

    executor, state, schema, config = ready(seeded, tmp_path)
    state["fault"] = "after_external_before_receipt"
    key = idempotency_key(state["branch_id"], logical_action_id("publish"))
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        graph = build_graph(executor, saver)
        with pytest.raises(InjectedCrash):
            graph.invoke(state, config)
        with executor.journal.sessions() as session:
            effect = session.scalar(select(Effect).where(Effect.tool_name == "mock.create"))
        executor.reconcile(effect.effect_id, owner_stopped=True)
        assert graph.invoke(None, config)["result"] == "SUCCEEDED"
        assert executor.external.request_count(key) == executor.external.truth_count(key) == 1
        forks = Forks(executor, saver)
        point = next(p for p in forks.history(state["run_id"]) if p["stage"] == "test")
        child = forks.create(state["run_id"], point["checkpoint_id"])
        final = graph.invoke(None, {"configurable": {"thread_id": child}})
        assert final["result"] == "SUCCEEDED"
        proof = executor.publications.require(child, final["branch_id"])
        assert proof["test_origin_run_id"] == state["run_id"]
        assert executor.external.request_count(key) == executor.external.truth_count(key) == 1
        child_key = idempotency_key(final["branch_id"], logical_action_id("publish"))
        assert executor.external.request_count(child_key) == 0

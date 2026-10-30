import pytest
from sqlalchemy import select
from test_api_postgres import client_for

from relayforge.config import Settings
from relayforge.db import Database
from relayforge.journal.service import Journal
from relayforge.journal.state import EffectClass, RunCancelled, Status
from relayforge.models import Effect, Run
from relayforge.providers.mock_external import InjectedCrash, MockExternal
from relayforge.runtime.cancellation import Cancellation
from relayforge.runtime.executor import ApprovalRequired, ToolExecutor
from relayforge.runtime.worker_lock import LocalWorkerLock
from relayforge.worker import Worker

pytestmark = pytest.mark.postgres


def worker_for(seeded, tmp_path, lock):
    engine, sessions, _ = seeded
    settings = Settings(
        workspace_root=tmp_path / "workspaces", mock_external_path=tmp_path / "truth.sqlite3"
    )
    database = Database(settings)
    database.close()
    database.engine, database.sessions = engine, sessions
    return Worker(database, settings, lock)


def test_cancelled_queue_is_not_claimed_and_cannot_dispatch(seeded, tmp_path):
    _, sessions, ids = seeded
    run_id, branch_id = ids
    journal = Journal(sessions)
    effect = journal.prepare(
        run_id,
        branch_id,
        "a",
        "mock.create",
        EffectClass.IDEMPOTENT_WRITE,
        {"subject": "a"},
        "allow",
    )
    cancel = Cancellation(sessions)
    assert cancel.request(run_id) == "CANCELLED"
    assert cancel.request(run_id) == "CANCELLED"
    assert journal.dispatch(effect.effect_id, authorized=True) is False
    executor = ToolExecutor(journal, MockExternal(tmp_path / "truth.sqlite3"))
    with pytest.raises(RunCancelled):
        executor.execute(*ids, "a", "mock.create", {"subject": "a"})
    assert executor.external.request_count(effect.idempotency_key) == 0
    assert journal.get(effect.effect_id).status == Status.PREPARED
    with LocalWorkerLock(tmp_path / "worker.lock") as lock:
        assert worker_for(seeded, tmp_path, lock).run_once(run_id) is False


def test_active_cancel_preserves_external_truth_and_unknown_after_crash(seeded, tmp_path):
    _, sessions, ids = seeded
    with sessions.begin() as session:
        session.get(Run, ids[0]).status = "RUNNING"
    executor = ToolExecutor(Journal(sessions), MockExternal(tmp_path / "truth.sqlite3"))
    with pytest.raises(InjectedCrash):
        executor.execute(
            *ids, "a", "mock.create", {"subject": "a"}, "after_external_before_receipt"
        )
    with sessions() as session:
        effect = session.scalar(select(Effect))
    assert Cancellation(sessions).request(ids[0]) == "CANCEL_REQUESTED"
    with LocalWorkerLock(tmp_path / "worker.lock") as lock:
        worker = worker_for(seeded, tmp_path, lock)
        assert worker.run_once(ids[0]) is True
        worker.mark(ids[0], "SUCCEEDED", "late_completion_must_not_override_cancel")
    with sessions() as session:
        assert session.get(Run, ids[0]).status == "CANCELLED"
    assert executor.journal.get(effect.effect_id).status == Status.UNKNOWN
    assert executor.external.truth_count(effect.idempotency_key) == 1
    assert executor.external.request_count(effect.idempotency_key) == 1
    # Cancellation stops future work; evidence reconciliation remains available.
    assert executor.reconcile(effect.effect_id, owner_stopped=True).status == Status.CONFIRMED


def test_api_cancel_revokes_pending_execution_and_refuses_retroactive_success(seeded, tmp_path):
    engine, sessions, ids = seeded
    executor = ToolExecutor(Journal(sessions), MockExternal(tmp_path / "truth.sqlite3"))
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "a", "mock.notify", {"subject": "a"})
    with sessions() as session:
        effect_id = session.scalar(select(Effect)).effect_id
    with client_for((engine, sessions), tmp_path) as client:
        assert client.post(f"/api/runs/{ids[0]}/cancel").json()["status"] == "CANCELLED"
        assert client.post(f"/api/effects/{effect_id}/approve").status_code == 409
        assert client.post(f"/api/runs/{ids[0]}/cancel").status_code == 200
        assert (
            client.post(
                f"/api/runs/{ids[0]}/replay", headers={"Origin": "https://untrusted.example"}
            ).status_code
            == 403
        )
        with sessions.begin() as session:
            session.get(Run, ids[0]).status = "SUCCEEDED"
        assert client.post(f"/api/runs/{ids[0]}/cancel").status_code == 409
        assert client.get(f"/api/runs/{ids[0]}").json()["status"] == "SUCCEEDED"

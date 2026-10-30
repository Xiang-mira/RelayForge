from types import SimpleNamespace

import pytest
from sqlalchemy import select

from relayforge.config import Settings
from relayforge.journal.service import Journal
from relayforge.models import Effect, Run, RunEvent
from relayforge.runtime.worker_lock import LocalWorkerLock
from relayforge.supervisor import Supervisor

pytestmark = pytest.mark.postgres


def test_unexpected_worker_exit_stays_unknown_and_repeated_failures_require_review(
    seeded, tmp_path, monkeypatch
):
    import relayforge.supervisor as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    _, sessions, (run_id, branch_id) = seeded
    journal = Journal(sessions)
    effect = journal.prepare(
        run_id,
        branch_id,
        "publish",
        "mock.create",
        "IDEMPOTENT_WRITE",
        {"subject": "test"},
        "allow",
    )
    with sessions.begin() as session:
        session.get(Run, run_id).status = "RUNNING"
    journal.dispatch(effect.effect_id, authorized=True)
    supervisor = Supervisor(SimpleNamespace(sessions=sessions), Settings(), None)
    with LocalWorkerLock(tmp_path / ".local/worker.lock"):
        supervisor.finish_worker(run_id, 1)
        with sessions() as session:
            assert session.get(Effect, effect.effect_id).status == "IN_FLIGHT"
            assert not session.scalars(
                select(RunEvent).where(RunEvent.type == "worker.process_exit")
            ).all()
    supervisor.finish_worker(run_id, 1)
    with sessions() as session:
        assert session.get(Effect, effect.effect_id).status == "UNKNOWN"
        assert session.get(Run, run_id).status == "RUNNING"
    supervisor.finish_worker(run_id, 1)
    supervisor.finish_worker(run_id, 1)
    with sessions() as session:
        assert session.get(Run, run_id).status == "NEEDS_REVIEW"
        assert session.get(Effect, effect.effect_id).status == "UNKNOWN"


def test_invalid_opt_in_provider_fails_before_any_tool_without_secret_logging(pg, tmp_path):
    from relayforge.config import ROOT
    from relayforge.db import Database
    from relayforge.runtime.tasks import Tasks
    from relayforge.worker import Worker
    from relayforge.workspaces.manager import Workspaces

    settings = Settings(
        model_provider="remote_json",
        model_base_url=None,
        model_name=None,
        workspace_root=tmp_path / "workspaces",
        mock_external_path=tmp_path / "truth.sqlite3",
    )
    database = Database(settings)
    database.close()
    database.engine, database.sessions = pg
    _, run_id = Tasks(pg[1], Workspaces(settings.workspace_root), ROOT / "demo/sample_repo").create(
        "Invalid opt-in provider"
    )
    with LocalWorkerLock(tmp_path / "worker.lock") as lock:
        assert Worker(database, settings, lock).run_once(run_id)
    with pg[1]() as session:
        assert session.get(Run, run_id).status == "FAILED"
        assert not session.scalars(select(Effect)).all()
        assert any(
            row.data.get("reason") == "coding_provider_unavailable"
            for row in session.scalars(select(RunEvent))
        )

import json
import time
from types import SimpleNamespace

from relayforge.config import Settings
from relayforge.runtime.presence import WorkerPresence, presence_path, read_presence


def test_initial_heartbeat_io_failure_does_not_abort_owned_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from relayforge.runtime.worker_lock import LocalWorkerLock
    worker = SimpleNamespace(owner="presence-failure-fixture", current_run=None)
    with LocalWorkerLock(tmp_path / "worker.lock") as lock:
        worker.lock = lock
        presence = WorkerPresence(worker, path=tmp_path / "worker-status.json")
        def unavailable(state="running"):
            raise PermissionError("simulated Windows sharing violation")
        monkeypatch.setattr(presence, "write", unavailable)
        with presence:
            assert lock.locked and presence.thread.is_alive()
        assert lock.locked
from relayforge.runtime.worker_lock import LocalWorkerLock


def test_recent_worker_signal_stops_after_context_and_stale_is_not_ownership(tmp_path):
    settings = Settings(workspace_root=tmp_path / "workspaces")
    assert read_presence(settings)["signal"] == "unavailable"
    with LocalWorkerLock(tmp_path / "worker.lock") as lock:
        worker = SimpleNamespace(
            settings=settings, owner="test-owner", current_run="test-run", lock=lock
        )
        with WorkerPresence(worker):
            signal = read_presence(settings)
            assert signal["signal"] == "recent_heartbeat"
            assert signal["run_id"] == "test-run"
        assert read_presence(settings)["signal"] == "not_recent"
        row = json.loads(presence_path(settings).read_text())
        row.update(timestamp=time.time() - 60, state="running")
        presence_path(settings).write_text(json.dumps(row))
        assert read_presence(settings)["signal"] == "not_recent"
        # Presence never releases or replaces the actual exclusive OS lock.
        assert lock.locked


def test_malformed_worker_signal_is_unavailable_without_path_details(tmp_path):
    settings = Settings(workspace_root=tmp_path / "workspaces")
    presence_path(settings).write_text('{"timestamp":"secret-invalid-value"}')
    assert read_presence(settings) == {
        "signal": "unavailable",
        "last_seen": None,
        "owner": None,
        "process_id": None,
        "run_id": None,
    }

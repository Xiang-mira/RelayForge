"""Local worker liveness hint; never used to transfer dispatch ownership."""

import json
import os
import threading
import time
from datetime import UTC, datetime


def presence_path(settings):
    return settings.workspace_root.parent / "worker-status.json"


def read_worker_presence(settings):
    try:
        row = json.loads(presence_path(settings).read_text(encoding="utf-8"))
        age = time.time() - float(row["timestamp"])
        return {
            "signal": "recent_heartbeat"
            if row["state"] == "running" and 0 <= age < 10
            else "not_recent",
            "last_seen": datetime.fromtimestamp(row["timestamp"], UTC).isoformat(),
            "owner": row["owner"],
            "process_id": row["process_id"],
            "run_id": row["run_id"],
            "activity": row.get("activity", "worker"),
            "suite_id": row.get("suite_id"),
            "note": "Heartbeat is a liveness hint, never dispatch ownership evidence",
        }
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {
            "signal": "unavailable",
            "last_seen": None,
            "owner": None,
            "process_id": None,
            "run_id": None,
        }


def read_presence(settings):
    path = settings.workspace_root.parent / "scheduler-status.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(row["timestamp"])
        if row["state"] == "running" and 0 <= age < 10:
            return {
                "signal": "recent_heartbeat",
                "source": "supervisor",
                "last_seen": datetime.fromtimestamp(row["timestamp"], UTC).isoformat(),
                "owner": row["owner"],
                "process_id": row["process_id"],
                "run_id": row["run_id"],
                "activity": row.get("activity"),
                "suite_id": row.get("suite_id"),
                "note": "Liveness hint only; dispatch still requires the child's actual OS lock",
            }
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return read_worker_presence(settings)


class WorkerPresence:
    def __init__(self, worker, path=None):
        self.worker = worker
        self.path = path or presence_path(worker.settings)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def write(self, state="running"):
        row = {
            "owner": self.worker.owner,
            "process_id": os.getpid(),
            "timestamp": time.time(),
            "run_id": self.worker.current_run,
            "state": state,
            "activity": getattr(self.worker, "activity", "worker"),
            "suite_id": getattr(self.worker, "current_suite", None),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(row), encoding="utf-8")
        temporary.replace(self.path)

    def loop(self):
        while not self.stop_event.wait(2):
            try:
                self.write()
            except OSError:
                # A stale signal is exposed honestly; it never grants ownership.
                pass

    def __enter__(self):
        if not self.worker.lock.locked:
            raise RuntimeError("Presence requires the exclusive worker lock")
        try:
            self.write()
        except OSError:
            # On Windows a concurrent reader may briefly block atomic replace.
            # A missing liveness hint must not abort an exclusively owned recovery.
            pass
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        self.thread.join(timeout=3)
        try:
            self.write("stopped")
        except OSError:
            pass

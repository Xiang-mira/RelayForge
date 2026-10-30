"""OS-released exclusive lock for one cooperating worker on this local project."""

import os
from pathlib import Path

from relayforge.workspaces.manager import reject_links


class WorkerAlreadyRunning(RuntimeError):
    pass


class LocalWorkerLock:
    def __init__(self, path: Path):
        reject_links(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        if path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.locked = False

    def __enter__(self):
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise WorkerAlreadyRunning(
                "Another local RelayForge worker holds the OS lock"
            ) from None
        self.locked = True
        return self

    def __exit__(self, *_):
        if self.locked:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.locked = False

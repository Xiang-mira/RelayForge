import subprocess
import sys
import time

import pytest

from relayforge.runtime.worker_lock import LocalWorkerLock, WorkerAlreadyRunning


def test_second_process_cannot_claim_active_worker_and_os_releases_lock(tmp_path):
    path = tmp_path / "worker.lock"
    program = "from pathlib import Path; from relayforge.runtime.worker_lock import LocalWorkerLock,WorkerAlreadyRunning; import sys\ntry:\n with LocalWorkerLock(Path(sys.argv[1])): pass\nexcept WorkerAlreadyRunning: sys.exit(7)"
    with LocalWorkerLock(path):
        result = subprocess.run(
            [sys.executable, "-c", program, str(path)], capture_output=True, check=False
        )
        assert result.returncode == 7
    result = subprocess.run(
        [sys.executable, "-c", program, str(path)], capture_output=True, check=False
    )
    assert result.returncode == 0


def test_hard_process_crash_releases_lock_without_a_stale_file_heuristic(tmp_path):
    path = tmp_path / "worker.lock"
    program = "from pathlib import Path; from relayforge.runtime.worker_lock import LocalWorkerLock; import sys,time\nwith LocalWorkerLock(Path(sys.argv[1])):\n print('locked',flush=True)\n time.sleep(60)"
    process = subprocess.Popen([sys.executable, "-c", program, str(path)], stdout=subprocess.PIPE)
    try:
        assert process.stdout.readline().strip() == b"locked"
        with pytest.raises(WorkerAlreadyRunning), LocalWorkerLock(path):
            pass
        process.kill()
        process.wait(timeout=5)
        # Windows can signal process exit just before final handle/lock cleanup.
        deadline = time.monotonic() + 2
        while True:
            try:
                with LocalWorkerLock(path):
                    assert path.exists()  # File presence does not indicate ownership.
                break
            except WorkerAlreadyRunning:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()

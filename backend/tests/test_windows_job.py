import os
import subprocess
import sys
import time

import pytest

from relayforge.runtime.windows_job import WindowsJob

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Requires actual Windows Job object process lifetime verification"
)


def test_owned_child_has_real_exit_code_and_hidden_redirected_logs(tmp_path):
    with WindowsJob() as job:
        child = job.spawn(
            [sys.executable, "-c", "print('owned child',flush=True);raise SystemExit(37)"],
            tmp_path,
            tmp_path / "out.log",
            tmp_path / "err.log",
        )
        try:
            deadline = time.monotonic() + 5
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert child.poll() == 37
            assert "owned child" in (tmp_path / "out.log").read_text()
        finally:
            child.close()


def test_hard_killed_parent_closes_job_and_terminates_owned_grandchild(tmp_path):
    marker = tmp_path / "grandchild.pid"
    ready = tmp_path / "ready"
    grandchild = "from pathlib import Path;import os,sys,time;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)"
    parent = "from pathlib import Path;from relayforge.runtime.windows_job import WindowsJob;import sys,time\nwith WindowsJob() as job:\n child=job.spawn([sys.executable,'-c',sys.argv[1],sys.argv[2]],Path(sys.argv[3]),Path(sys.argv[3])/'out',Path(sys.argv[3])/'err')\n Path(sys.argv[4]).write_text('ready')\n time.sleep(60)"
    process = subprocess.Popen(
        [sys.executable, "-c", parent, grandchild, str(marker), str(tmp_path), str(ready)]
    )
    process_handle = None
    try:
        deadline = time.monotonic() + 8
        while not (ready.exists() and marker.exists()) and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.02)
        assert ready.exists() and marker.exists()
        from ctypes import wintypes

        from relayforge.runtime.windows_job import kernel

        open_process = kernel().OpenProcess
        open_process.argtypes, open_process.restype = (
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        )
        process_handle = open_process(
            0x00100000, False, int(marker.read_text())
        )  # SYNCHRONIZE only.
        assert process_handle
        process.kill()  # Exact spawned process handle, never an enumerated unrelated PID.
        process.wait(timeout=5)
        assert kernel().WaitForSingleObject(process_handle, 5000) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if process_handle:
            kernel().CloseHandle(process_handle)

from uuid import uuid4

import pytest

from relayforge.workspaces.manager import UnsafePath, Workspaces
from relayforge.workspaces.sandbox import DockerSandbox
from relayforge.workspaces.sandbox import TestResult as SandboxResult


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        "/absolute",
        "C:/Windows/a",
        "a\\b",
        "a/../b",
        "a/./b",
        "NUL",
        "CON.txt",
        "a/ends.",
    ],
)
def test_path_escape_is_rejected(tmp_path, relative):
    workspaces = Workspaces(tmp_path / "workspaces")
    with pytest.raises(UnsafePath):
        workspaces.write(str(uuid4()), relative, "forbidden")


def test_workspace_is_independent_and_never_overwritten(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("value=1\n")
    workspaces = Workspaces(tmp_path / "workspaces")
    parent, child = str(uuid4()), str(uuid4())
    workspaces.create(parent, source)
    workspaces.create(child, workspaces.branch_path(parent))
    workspaces.write(child, "a.py", "value=2\n")
    assert workspaces.read(parent, "a.py") == "value=1\n"
    assert "+value=2" in workspaces.diff(child, "a.py", "value=1\n")
    with pytest.raises(FileExistsError):
        workspaces.create(parent, source)


def test_sandbox_uses_fixed_command_and_resource_limits(tmp_path):
    workspaces = Workspaces(tmp_path)
    branch = str(uuid4())
    workspaces.branch_path(branch).mkdir()
    command = DockerSandbox(workspaces).command(branch, "relayforge-test-owned")
    assert command[-6:] == ["python", "-I", "-m", "unittest", "discover", "-v"]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "65532:65532"
    assert "--read-only" in command and "--privileged" not in command
    assert not any("docker.sock" in value for value in command)
    assert "--memory" in command and "--cpus" in command and "--pids-limit" in command


def test_zero_tests_or_nonzero_exit_never_pass_acceptance():
    assert not SandboxResult(0, "Ran 0 tests in 0s\nOK").passed
    assert not SandboxResult(1, "Ran 3 tests in 0s\nFAILED").passed
    assert not SandboxResult(0, "Ran 3 tests in 0s\nOK", timed_out=True).passed
    assert SandboxResult(0, "Ran 3 tests in 0s\nOK").passed


def test_real_directory_link_cannot_escape_managed_workspace(tmp_path):
    import os
    import subprocess

    workspaces = Workspaces(tmp_path / "workspaces")
    branch = str(uuid4())
    workspaces.branch_path(branch).mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspaces.branch_path(branch) / "escape"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert created.returncode == 0, "Test-owned junction creation failed"
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(UnsafePath):
            workspaces.write(branch, "escape/stolen.txt", "blocked")
        assert not (outside / "stolen.txt").exists()
    finally:
        if os.name == "nt":
            os.rmdir(link)  # Remove only this test-created junction, not its target.
        else:
            link.unlink()

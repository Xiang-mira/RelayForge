from uuid import uuid4

import pytest

from relayforge.workspaces.manager import UnsafePath, Workspaces
from relayforge.workspaces.snapshots import Snapshots


def test_historical_snapshot_is_verified_and_independent(tmp_path):
    workspaces = Workspaces(tmp_path / "workspaces")
    branch, child = str(uuid4()), str(uuid4())
    workspaces.write(branch, "calculator.py", "original\n")
    snapshots = Snapshots(workspaces)
    identity = snapshots.capture(branch)
    workspaces.write(branch, "calculator.py", "later\n")
    snapshots.restore(identity, child)
    assert workspaces.read(child, "calculator.py") == "original\n"
    workspaces.write(child, "calculator.py", "child-only\n")
    assert workspaces.read(branch, "calculator.py") == "later\n"
    (snapshots.path(identity) / "files" / "calculator.py").write_text("tampered")
    with pytest.raises(ValueError):
        snapshots.verified_source(identity)


def test_snapshot_refuses_credential_files(tmp_path):
    workspaces = Workspaces(tmp_path / "workspaces")
    branch = str(uuid4())
    workspaces.write(branch, ".env", "not-an-actual-key")
    with pytest.raises(UnsafePath):
        Snapshots(workspaces).capture(branch)

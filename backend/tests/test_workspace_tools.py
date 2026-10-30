from uuid import uuid4

import pytest

from relayforge.journal.state import idempotency_key, payload_hash
from relayforge.providers.mock_external import InjectedCrash
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.workspaces.manager import Workspaces


def provider(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "calculator.py").write_text("def add(a,b): return a-b\n")
    workspaces = Workspaces(tmp_path / "workspaces")
    branch = str(uuid4())
    workspaces.create(branch, source)
    return WorkspaceTools(workspaces), branch


def test_read_receipt_is_durable_and_does_not_silently_read_new_state(tmp_path):
    tools, branch = provider(tmp_path)
    key = idempotency_key(branch, "read")
    receipt = tools.execute("workspace.read", branch, {"path": "calculator.py"}, key)
    tools.workspaces.write(branch, "calculator.py", "changed\n")
    restarted = WorkspaceTools(tools.workspaces)
    assert "a-b" in restarted.result(receipt)["content"]


def test_write_crash_can_be_reconciled_by_desired_content(tmp_path):
    tools, branch = provider(tmp_path)
    key = idempotency_key(branch, "write")
    payload = {"path": "calculator.py", "content": "def add(a,b): return a+b\n"}
    with pytest.raises(InjectedCrash):
        tools.execute("workspace.write", branch, payload, key, "after_workspace_write")
    assert tools.lookup(key, payload_hash(payload)).outcome == "indeterminate"
    evidence = tools.lookup(key, payload_hash(payload), quiescent=True)
    assert evidence.outcome == "occurred"
    assert tools.result(evidence.receipt)["content_hash"]


def test_managed_tests_and_arbitrary_host_files_are_not_rewritable(tmp_path):
    tools, branch = provider(tmp_path)
    with pytest.raises(ValueError):
        tools.validate("workspace.write", branch, {"path": "test_calculator.py", "content": "pass"})
    with pytest.raises(ValueError):
        tools.validate("workspace.read", branch, {"path": ".env"})
    with pytest.raises(ValueError):
        tools.result({"provider": "managed_workspace", "external_id": "../outside"})


def test_incomplete_test_is_never_inferred_successful(tmp_path):
    tools, branch = provider(tmp_path)
    key = idempotency_key(branch, "test")
    tools._save(
        key, {"payload_hash": payload_hash({}), "tool": "workspace.test", "completed": False}
    )
    assert tools.lookup(key, payload_hash({}), quiescent=True).outcome == "indeterminate"

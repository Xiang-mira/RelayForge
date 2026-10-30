import sqlite3
from unittest.mock import Mock

import pytest

from relayforge.workspaces.sandbox import TestResult as SandboxResult
from relayforge.config import ROOT
from test_controlled_postgres import service, prepare


def test_pytest_summary_requires_executed_tests():
    assert SandboxResult(0, "================ 29 passed in 0.01s ================\n").passed
    failed = SandboxResult(1, "================ 17 failed, 12 passed in 0.04s ================\n")
    assert failed.test_count == 29 and not failed.passed
    assert not SandboxResult(0, "================ 29 skipped in 0.01s ================\n").passed
    assert not SandboxResult(0, "no tests ran").passed


@pytest.mark.postgres
@pytest.mark.docker
def test_new_model_demo_uses_registered_pytest_and_refuses_failed_finish(pg, tmp_path):
    svc = service(pg, tmp_path)
    bound = svc.invoke("begin_task", {"template": "mcp_header_parser", "title": "Parser gate proof"})
    assert bound["status"] == "OK"
    assert bound["data"]["scope"]["test_configuration"] == "pytest"
    ctx = {"context_id": bound["data"]["context_id"]}
    action = prepare(svc, ctx, "baseline", "run_tests")
    assert svc.invoke("run_tests", {**action, "configuration": "unittest"})["status"] == "DENIED"
    action = prepare(svc, ctx, "actual-pytest", "run_tests")
    result = svc.invoke("run_tests", {**action, "configuration": "pytest"})
    assert result["status"] == "FAILED", result
    assert result["data"]["test_count"] == 29 and result["data"]["exit_code"] == 1
    assert "FAILED test_calculator.py" in result["data"]["stdout"]
    assert result["data"]["acceptance"]["configuration"]["image"] == "relayforge-sandbox:pytest-header-v1"
    finish = prepare(svc, ctx, "premature", "finish_task")
    assert svc.invoke("finish_task", finish)["status"] == "NEEDS_REVALIDATION"
    for path in ("test_calculator.py", ".relayforge-test-profile"):
        change = prepare(svc, ctx, "forbidden-" + path, "apply_patch")
        assert svc.invoke("apply_patch", {**change, "path": path, "content": "changed",
                                         "expected_hash": "0" * 64})["status"] == "DENIED"
    with sqlite3.connect(svc.settings.mock_external_path) as connection:
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 0


@pytest.mark.postgres
@pytest.mark.docker
def test_pytest_evidence_cannot_publish_changed_source(pg, tmp_path, monkeypatch):
    svc = service(pg, tmp_path)
    # A separate tiny trusted pytest fixture checks the profile/gate invariant;
    # it supplies no repair code to the real model experiment.
    fixture = tmp_path / "fixtures/mcp_header_parser"
    fixture.mkdir(parents=True)
    (fixture / ".relayforge-test-profile").write_bytes(
        (ROOT / "demo/mcp_header_parser/.relayforge-test-profile").read_bytes())
    (fixture / "calculator.py").write_text("answer = 42\n", encoding="utf-8")
    (fixture / "test_calculator.py").write_text(
        "from pathlib import Path\n"
        "def test_answer():\n"
        "    scope = {}\n"
        "    exec(Path('/workspace/calculator.py').read_text(), scope)\n"
        "    assert scope['answer'] == 42\n", encoding="utf-8")
    svc.tasks.demo = fixture.parent / "sample_repo"
    bound = svc.invoke("begin_task", {"template": "mcp_header_parser"})
    ctx = {"context_id": bound["data"]["context_id"]}
    tests = prepare(svc, ctx, "passed-pytest", "run_tests")
    assert svc.invoke("run_tests", {**tests, "configuration": "pytest"})["data"]["passed"]
    source = svc.invoke("read_file", ctx)["data"]
    patch = prepare(svc, ctx, "source-changed", "apply_patch")
    assert svc.invoke("apply_patch", {**patch, "path": "calculator.py", "expected_hash": source["sha256"],
                                      "content": source["content"] + "# changed after test\n"})["status"] == "OK"
    external = Mock(wraps=svc.executor.external.execute)
    monkeypatch.setattr(svc.executor.external, "execute", external)
    finish = prepare(svc, ctx, "stale-finish", "finish_task")
    refused = svc.invoke("finish_task", finish)
    assert refused["status"] == "NEEDS_REVALIDATION" and "workspace_changed" in refused["message"]
    external.assert_not_called()
    with sqlite3.connect(svc.settings.mock_external_path) as connection:
        assert connection.execute("SELECT count(*) FROM requests").fetchone()[0] == 0

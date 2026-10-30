import sqlite3

import pytest
from sqlalchemy import select

from relayforge.config import Settings
from relayforge.models import Effect
from relayforge.policies.service import Approvals
from relayforge.runtime.cancellation import Cancellation
from relayforge.runtime.controlled import ControlledTasks
from relayforge.runtime.worker_lock import LocalWorkerLock

pytestmark = pytest.mark.postgres


def service(pg, tmp_path, **options):
    settings = Settings(workspace_root=tmp_path / "workspaces",
                        mock_external_path=tmp_path / "external.sqlite3",
                        mcp_token_path=tmp_path / "bridge.token")
    return ControlledTasks(pg[1], settings, **options)


def new_context(svc):
    bound = svc.invoke("begin_task", {})
    assert bound["status"] == "OK"
    return bound, {"context_id": bound["data"]["context_id"]}


def prepare(svc, context, key, operation):
    result = svc.invoke("prepare_action", {**context, "request_key": key, "operation": operation})
    assert result["status"] == "OK", result
    return {**context, "action_id": result["data"]["action_id"]}


def test_context_scope_identity_preconditions_and_budget_survive_new_service(pg, tmp_path):
    svc = service(pg, tmp_path)
    first, ctx = new_context(svc)
    with LocalWorkerLock(svc.lock_path):
        assert svc.invoke("read_file", ctx)["status"] == "BUSY"
    _other, other_ctx = new_context(svc)
    assert svc.invoke("read_file", {"context_id": "0" * 64})["status"] == "INVALID_CONTEXT"
    for path in ("../.env", "C:/Windows/win.ini", ".env", "test_calculator.py/../calculator.py"):
        assert svc.invoke("read_file", {**ctx, "path": path})["status"] == "DENIED"
    action = prepare(svc, ctx, "proposal-one", "apply_patch")
    restarted = service(pg, tmp_path)
    again = prepare(restarted, ctx, "proposal-one", "apply_patch")
    assert again == action
    current = svc.invoke("read_file", ctx)["data"]
    patch = {**action, "path": "calculator.py", "expected_hash": current["sha256"],
             "content": current["content"] + "\n# proposal one\n"}
    assert svc.invoke("apply_patch", {**patch, **other_ctx})["status"] == "DENIED"
    assert svc.invoke("apply_patch", patch)["status"] == "OK"
    assert restarted.invoke("apply_patch", patch)["status"] == "OK"
    assert svc.invoke("apply_patch", {**patch, "content": "different"})["status"] == "CONFLICT"
    stale = prepare(svc, ctx, "stale-version", "apply_patch")
    assert svc.invoke("apply_patch", {**patch, **stale})["status"] == "CONFLICT"
    assert svc.invoke("read_file", other_ctx)["data"]["content"] == current["content"]
    with pg[1]() as session:
        effects = session.scalars(select(Effect).where(Effect.logical_action_id == action["action_id"])).all()
        assert len(effects) == 1 and effects[0].attempt_count == 1 and effects[0].status == "CONFIRMED"
    tiny = service(pg, tmp_path, limits={"calls": 2, "tests": 1, "seconds": 600})
    _, tiny_ctx = new_context(tiny)
    assert tiny.invoke("read_file", {**tiny_ctx, "path": ".env"})["status"] == "DENIED"
    assert tiny.invoke("read_file", tiny_ctx)["status"] == "OK"
    assert service(pg, tmp_path).invoke("begin_task", tiny_ctx)["data"]["budget"]["calls_left"] == 0
    assert tiny.invoke("read_file", tiny_ctx)["status"] == "BUDGET_EXCEEDED"
    limited_tests = service(pg, tmp_path, limits={"calls": 5, "tests": 0, "seconds": 600})
    limited, limited_ctx = new_context(limited_tests)
    test = prepare(limited_tests, limited_ctx, "test", "run_tests")
    assert limited_tests.invoke("run_tests", test)["status"] == "BUDGET_EXCEEDED"
    limited_tests.update(limited["run_id"], expires_at=0)
    assert limited_tests.invoke("read_file", limited_ctx)["status"] == "BUDGET_EXCEEDED"
    Cancellation(pg[1]).request(first["run_id"])
    assert svc.invoke("apply_patch", patch)["status"] == "TASK_FINISHED"


@pytest.mark.docker
def test_controlled_approval_unknown_and_selected_test_keep_real_gate(pg, tmp_path, monkeypatch):
    svc = service(pg, tmp_path, publish_tool="mock.notify")
    _bound, ctx = new_context(svc)
    current = svc.invoke("read_file", ctx)["data"]
    patch = prepare(svc, ctx, "patch", "apply_patch")
    assert svc.invoke("apply_patch", {**patch, "path": "calculator.py", "expected_hash": current["sha256"],
                                      "content": current["content"].replace("return 0", "return 0 if total >= 100 else 8")})["status"] == "OK"
    tests = prepare(svc, ctx, "tests", "run_tests")
    passed = svc.invoke("run_tests", tests)
    assert passed["status"] == "OK" and passed["data"]["exit_code"] == 0
    assert passed["data"]["stderr"] and passed["data"]["acceptance"]["test_run_id"]
    finish = prepare(svc, ctx, "finish", "finish_task")
    for _ in range(2):
        assert svc.invoke("finish_task", finish)["status"] == "NEEDS_APPROVAL"
    from relayforge.runtime.evidence import RuntimeEvidence
    assert RuntimeEvidence(pg[1], svc.settings).view(_bound["run_id"])["results"]["effect_safety"]["state"] == "pending_approval"
    with pg[1]() as session:
        effect = session.scalar(select(Effect).where(Effect.logical_action_id == finish["action_id"]))
    assert svc.executor.external.request_count(effect.idempotency_key) == 0
    Approvals(pg[1]).decide(effect.effect_id, "APPROVED")
    original = svc.executor.external.execute

    def lose(*args, **kwargs):
        return original(*args[:3], fault="response_lost")

    monkeypatch.setattr(svc.executor.external, "execute", lose)
    assert svc.invoke("finish_task", finish)["status"] == "UNKNOWN"
    assert RuntimeEvidence(pg[1], svc.settings).view(_bound["run_id"])["results"]["effect_safety"]["state"] == "unresolved"
    svc.executor.external.supports_query = False
    assert svc.invoke("finish_task", finish)["status"] == "UNKNOWN"
    next_action = prepare(svc, ctx, "next-patch", "apply_patch")
    assert svc.invoke("apply_patch", {**next_action, "path": "calculator.py", "expected_hash": current["sha256"],
                                      "content": "must not be written"})["status"] == "UNKNOWN"
    assert svc.executor.external.request_count(effect.idempotency_key) == 1
    monkeypatch.setattr(svc.executor.external, "execute", original)
    svc.executor.external.supports_query = True
    assert svc.invoke("finish_task", finish)["data"]["result"] == "SUCCEEDED"
    assert svc.executor.external.request_count(effect.idempotency_key) == svc.executor.external.truth_count(effect.idempotency_key) == 1
    # Independent evaluator view; not supplied to recovery decisions.
    with sqlite3.connect(svc.settings.mock_external_path.as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert svc.invoke("get_execution_status", ctx)["data"]["unknown"] == []

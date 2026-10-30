import json

import pytest
from sqlalchemy import select

from relayforge.journal.state import Conflict
from relayforge.models import Effect, RunEvent
from relayforge.runtime.evidence import RuntimeEvidence
from test_controlled_postgres import service, new_context, prepare

pytestmark = pytest.mark.postgres


def test_context_cannot_be_written_or_logged_as_source(pg, tmp_path):
    svc = service(pg, tmp_path)
    bound, ctx = new_context(svc)
    original = svc.invoke("read_file", ctx)["data"]
    action = prepare(svc, ctx, "unsafe", "apply_patch")
    result = svc.invoke("apply_patch", {**action, "path": "calculator.py", "expected_hash": original["sha256"],
                                      "content": "# " + ctx["context_id"]})
    assert result["status"] == "DENIED"
    assert svc.workspaces.read(svc.context(ctx["context_id"]).branch_id, "calculator.py") == original["content"]
    with pg[1]() as session:
        assert session.scalar(select(Effect).where(Effect.run_id == bound["run_id"], Effect.tool_name == "workspace.write")) is None
        events = session.scalars(select(RunEvent).where(RunEvent.run_id == bound["run_id"])).all()
        assert ctx["context_id"] not in json.dumps([e.data for e in events])


def test_client_evidence_requires_exact_runtime_link_and_retains_origin(pg, tmp_path):
    svc = service(pg, tmp_path)
    bound, ctx = new_context(svc)
    read = svc.invoke("read_file", ctx)
    evidence = RuntimeEvidence(pg[1], svc.settings)
    payload = {"run_id": bound["run_id"], "session_id": "ses_test", "provider": "openai", "model": "recorded-test-model",
               "auth_route": "chatgpt_oauth", "source_hash": "a" * 64,
               "calls": [{"call_id": "client-read", "message_id": "msg_read", "tool": "relayforge_read_file",
                          "runtime_call_id": read["runtime_call_id"], "status": "OK"}],
               "messages": [{"message_id": "msg_text", "text": "Visible statement " + ctx["context_id"], "after_call_id": "client-read"}]}
    wrong = {**payload, "calls": [{**payload["calls"][0], "tool": "relayforge_finish_task"}]}
    with pytest.raises(ValueError, match="binding mismatch"):
        evidence.import_client(wrong)
    assert evidence.import_client(payload)["status"] == "RECORDED"
    assert evidence.import_client(payload)["status"] == "ALREADY_RECORDED"
    with pytest.raises(Conflict):
        evidence.import_client({**payload, "source_hash": "b" * 64})
    view = evidence.view(bound["run_id"])
    assert view["source"] == "model_client_export" and view["agent"]["origin"] == "opencode_client_export"
    assert view["client_calls"][0]["runtime_event_id"] == read["runtime_event_id"]
    assert ctx["context_id"] not in json.dumps(view)
    assert view["results"]["coding_task"]["state"] == "CONTROLLED"
    assert view["results"]["recovery_correctness"]["state"] == "not_exercised"
    assert view["results"]["effect_safety"]["state"] == "not_exercised"
    assert not view["iteration"]["failure_after_patch_then_revision"]


def test_evidence_http_is_read_only_and_import_requires_bridge_authority(pg, tmp_path):
    from fastapi.testclient import TestClient
    from relayforge.api.app import create_app
    from relayforge.db import Database
    svc = service(pg, tmp_path)
    bound, _ctx = new_context(svc)
    database = Database(svc.settings)
    database.close()
    database.engine, database.sessions = pg
    with TestClient(create_app(svc.settings, database), base_url="http://127.0.0.1") as client:
        before = client.get("/api/runs/" + bound["run_id"]).json()
        assert before["execution_source"] == "controlled_tools"
        assert client.get("/api/runs/" + bound["run_id"] + "/evidence").status_code == 200
        assert client.get("/api/runs/" + bound["run_id"]).json() == before
        missing = client.get("/api/runs/00000000-0000-0000-0000-000000000000/evidence")
        assert missing.status_code == 404
        payload = {"run_id": bound["run_id"], "session_id": "ses_test", "provider": "openai", "model": "fixture",
                   "auth_route": "chatgpt_oauth", "source_hash": "a" * 64, "calls": [], "messages": []}
        assert client.post("/api/controlled/agent-evidence", json=payload).status_code == 403
        assert client.post("/api/controlled/import_client", json={"arguments": payload}).status_code == 403


def test_baseline_absent_journal_is_not_zero_external_effects(pg, tmp_path):
    from relayforge.models import Run, EvaluationSuite, EvaluationRun
    svc = service(pg, tmp_path)
    bound, _ = new_context(svc)
    with pg[1].begin() as session:
        suite = EvaluationSuite(name="isolated baseline view fixture", config={})
        session.add(suite)
        session.flush()
        evaluation = EvaluationRun(suite_id=suite.id, mode="baseline")
        session.add(evaluation)
        session.flush()
        run = session.get(Run, bound["run_id"])
        run.mode, run.evaluation_run_id = "baseline", evaluation.id
    safety = RuntimeEvidence(pg[1], svc.settings).view(bound["run_id"])["results"]["effect_safety"]
    assert safety["state"] == "not_verified" and safety["duplicate_operations"] is None

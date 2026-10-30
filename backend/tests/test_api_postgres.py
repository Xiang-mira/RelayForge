import pytest
from fastapi.testclient import TestClient

from relayforge.api.app import create_app
from relayforge.config import Settings
from relayforge.db import Database
from relayforge.journal.service import Journal
from relayforge.models import Run
from relayforge.providers.mock_external import MockExternal
from relayforge.runtime.executor import ApprovalRequired, ToolExecutor
from relayforge.runtime.faults import take_fault_once

pytestmark = pytest.mark.postgres


def client_for(pg, tmp_path):
    engine, sessions = pg
    settings = Settings(
        workspace_root=tmp_path / "workspaces", mock_external_path=tmp_path / "truth.sqlite3"
    )
    database = Database(settings)
    database.close()
    database.engine, database.sessions = engine, sessions
    return TestClient(create_app(settings, database), base_url="http://127.0.0.1")


def test_task_run_api_persists_and_does_not_accept_arbitrary_repositories(pg, tmp_path):
    with client_for(pg, tmp_path) as client:
        created = client.post("/api/tasks", json={"title": "Fix the sample addition bug"})
        assert created.status_code == 201
        ids = created.json()
        assert client.get("/api/tasks").json()[0]["id"] == ids["task_id"]
        run = client.get("/api/runs/" + ids["run_id"]).json()
        assert run["status"] == "QUEUED"
        assert client.get(f"/api/runs/{run['id']}/effects").json() == []
        assert client.post(f"/api/runs/{run['id']}/replay").status_code == 409
        assert (
            client.post("/api/tasks", json={"title": "bad", "repository": "C:/Windows"}).status_code
            == 422
        )
        assert client.get("/api/tasks/missing").status_code == 404
    with pg[1]() as session:
        assert session.get(Run, ids["run_id"]).status == "QUEUED"


def test_api_approval_is_bound_to_real_effect_and_payload(seeded, tmp_path):
    engine, sessions, ids = seeded
    executor = ToolExecutor(Journal(sessions), MockExternal(tmp_path / "truth.sqlite3"))
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "a", "mock.notify", {"subject": "notification"})
    from sqlalchemy import select

    from relayforge.models import Effect

    with sessions() as session:
        effect = session.scalar(select(Effect))
        effect_id = effect.effect_id
    with client_for((engine, sessions), tmp_path) as client:
        assert client.get(f"/api/effects/{effect_id}/approval").json()["decision"] == "PENDING"
        response = client.post(f"/api/effects/{effect_id}/approve")
        assert response.status_code == 200
        assert client.get(f"/api/effects/{effect_id}/approval").json()["decision"] == "APPROVED"
        assert client.post(f"/api/effects/{effect_id}/reject").status_code == 409
        receipt = executor.execute(*ids, "a", "mock.notify", {"subject": "notification"})
        view = client.get(f"/api/effects/{effect_id}").json()
        assert view["status"] == "CONFIRMED"
        assert view["external_receipt"] == receipt
        assert "notification" not in str(view)


def test_fault_injection_is_one_shot_across_concurrent_recovery(seeded):
    from concurrent.futures import ThreadPoolExecutor

    _, sessions, (run_id, _) = seeded
    with ThreadPoolExecutor(max_workers=8) as pool:
        observations = list(
            pool.map(lambda _: take_fault_once(sessions, run_id, "before_dispatch"), range(16))
        )
    assert observations.count("before_dispatch") == 1
    assert observations.count("none") == 15


def test_invalid_request_does_not_echo_secret_and_local_origin_is_enforced(pg, tmp_path):
    secret = "test-only-sensitive-value-do-not-echo"
    with client_for(pg, tmp_path) as client:
        rejected = client.post("/api/tasks", json={"title": "invalid", "api_key": secret})
        assert rejected.status_code == 422
        assert secret not in rejected.text
        assert (
            client.post(
                "/api/tasks", json={"title": "blocked"}, headers={"Origin": "null"}
            ).status_code
            == 403
        )
        assert client.get("/api/tasks", headers={"Host": "untrusted.example"}).status_code == 400


def test_console_queries_read_real_counts_and_empty_evaluations(pg, tmp_path):
    with client_for(pg, tmp_path) as client:
        assert client.get("/api/overview").json()["runs_by_status"] == {}
        assert client.get("/api/evaluations").json() == []
        assert client.get("/api/approvals").json() == []
        assert client.get("/api/evaluations/missing/results").status_code == 404
        client.post("/api/tasks", json={"title": "A real queue entry"}).raise_for_status()
        assert client.get("/api/overview").json()["runs_by_status"] == {"QUEUED": 1}
        assert client.get("/api/effects?status=UNKNOWN").json() == []
        assert client.get("/api/effects?status=INVENTED").status_code == 422
        assert client.get("/api/effects?limit=100000").status_code == 422


def test_template_catalog_creates_the_selected_real_source_and_rejects_paths(pg, tmp_path):
    from relayforge.workspaces.manager import Workspaces

    with client_for(pg, tmp_path) as client:
        catalog = client.get("/api/templates").json()
        assert len(catalog) >= 10 and len({row["id"] for row in catalog}) == len(catalog)
        created = client.post(
            "/api/tasks", json={"title": "Binary search edge", "template": "fix_binary_search"}
        )
        assert created.status_code == 201
        run = client.get("/api/runs/" + created.json()["run_id"]).json()
        assert "while low < high:" in Workspaces(tmp_path / "workspaces").read(
            run["branch_id"], "calculator.py"
        )
        assert (
            client.post(
                "/api/tasks", json={"title": "Invalid", "template": "../../Windows"}
            ).status_code
            == 422
        )


def test_evaluation_owned_runs_cannot_be_mutated_by_interactive_api(seeded, tmp_path):
    from sqlalchemy import select
    from test_evaluation_modes_postgres import setup_mode

    from relayforge.models import Effect

    executor, engine = setup_mode(seeded, tmp_path, "journal")
    sessions, ids = seeded[1], seeded[2]
    with pytest.raises(ApprovalRequired):
        executor.execute(*ids, "approval", "mock.notify", {"subject": "unit fixture"})
    with sessions.begin() as session:
        session.get(Run, ids[0]).status = "NEEDS_REVIEW"
        effect_id = session.scalar(select(Effect)).effect_id
    with client_for((engine, sessions), tmp_path) as client:
        for action in ["replay", "cancel"]:
            assert client.post(f"/api/runs/{ids[0]}/{action}").status_code == 409
        for action in ["approve", "reject", "reconcile"]:
            assert client.post(f"/api/effects/{effect_id}/{action}").status_code == 409
        assert client.get(f"/api/effects/{effect_id}/approval").json()["decision"] == "PENDING"
        assert (
            client.post(
                "/api/tasks", json={"title": "Forbidden mode", "mode": "baseline"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/tasks",
                json={"title": "Forbidden approval", "execution_config": {"preauthorized": True}},
            ).status_code
            == 422
        )


def test_evaluation_summary_reads_observations_without_filling_undefined_rates(pg, tmp_path):
    from relayforge.models import EvaluationCase, EvaluationResult, EvaluationRun, EvaluationSuite

    with pg[1].begin() as session:
        suite = EvaluationSuite(
            name="API-only unit fixture",
            config={
                "matrix_version": 1,
                "templates": ["fix_addition"],
                "faults": ["no_idempotency_no_query"],
                "repeats": 1,
            },
        )
        session.add(suite)
        session.flush()
        evaluation = EvaluationRun(suite_id=suite.id, mode="full", status="COMPLETED")
        case = EvaluationCase(
            suite_id=suite.id, template="fix_addition", fault="no_idempotency_no_query", seed=0
        )
        session.add_all([evaluation, case])
        session.flush()
        suite_id, evaluation_id = suite.id, evaluation.id
        session.add(
            EvaluationResult(
                evaluation_run_id=evaluation.id,
                case_id=case.id,
                observed={
                    "scope": "matrix",
                    "phase": "COMPLETED",
                    "mode": "full",
                    "template": "fix_addition",
                    "fault": "no_idempotency_no_query",
                    "run_status": "NEEDS_REVIEW",
                    "actual_external_effects": 1,
                    "external_requests": 1,
                    "unsafe_retry_count": 0,
                    "asserted_external_outcome": None,
                    "workflow_elapsed_s": 1.0,
                    "independent_acceptance": True,
                    "reported_test_passed": True,
                    "recovery_eligible": False,
                },
            )
        )
    with client_for(pg, tmp_path) as client:
        row = client.get("/api/evaluations").json()[0]
        assert row["id"] == evaluation_id and row["planned_cases"] == 1
        assert row["metrics"]["false_success_rate"]["value"] is None
        assert row["metrics"]["manual_review_rate"]["value"] == 1
        summary = client.get(f"/api/evaluation-suites/{suite_id}/summary").json()["summary"]
        assert summary["modes"]["full"]["coding_task_success"]["value"] == 0
        assert client.get(f"/api/evaluation-suites/{suite_id}/exports/csv").status_code == 404
        assert client.get(f"/api/evaluation-suites/{suite_id}/exports/env").status_code == 422
        schema = client.get("/openapi.json").json()
        assert (
            "fix_binary_search"
            in schema["components"]["schemas"]["TaskInput"]["properties"]["template"]["enum"]
        )


def test_evaluation_enqueue_persists_bounded_matrix_and_reports_unavailable(
    pg, tmp_path, monkeypatch
):
    from sqlalchemy import func, select

    from relayforge.evaluation.runner import ExperimentRunner
    from relayforge.models import EvaluationCase, EvaluationRun, EvaluationSuite

    # This test checks the durable queue contract; actual Docker preflight and execution
    # are independently exercised by the real HTTP/supervisor acceptance driver.
    monkeypatch.setattr(ExperimentRunner, "environment", lambda self: {"unit_fixture": True})
    with client_for(pg, tmp_path) as client:
        response = client.post(
            "/api/evaluations/run",
            json={
                "templates": ["fix_addition"],
                "faults": ["none", "response_lost"],
                "repeats": 2,
            },
        )
        assert response.status_code == 202
        row = response.json()
        assert row["status"] == "QUEUED" and row["planned_observations"] == 12
        with pg[1]() as session:
            suite = session.get(EvaluationSuite, row["suite_id"])
            assert suite.config["repeats"] == 2
            runs = session.scalars(select(EvaluationRun)).all()
            assert {run.mode for run in runs} == {"baseline", "journal", "full"}
            assert {run.status for run in runs} == {"QUEUED"}
            assert {run.id for run in runs} == set(row["evaluation_run_ids"])
            assert session.scalar(select(func.count()).select_from(EvaluationCase)) == 4
        for invalid in [
            {"templates": ["../outside"]},
            {"faults": ["unknown"]},
            {"templates": ["fix_addition", "fix_addition"]},
            {"faults": []},
            {"repeats": 0},
            {"repeats": 6},
            {"preauthorized": True},
        ]:
            assert client.post("/api/evaluations/run", json=invalid).status_code == 422

        def unavailable(self):
            raise RuntimeError("test environment unavailable")

        monkeypatch.setattr(ExperimentRunner, "environment", unavailable)
        assert client.post("/api/evaluations/run", json={}).status_code == 503
        with pg[1]() as session:
            assert session.scalar(select(func.count()).select_from(EvaluationSuite)) == 1

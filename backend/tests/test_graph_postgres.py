from pathlib import Path

import pytest
from sqlalchemy import text

from relayforge.config import Settings
from relayforge.journal.service import Journal
from relayforge.models import Run
from relayforge.providers.mock_external import KnownNoEffect, MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ToolExecutor
from relayforge.runtime.graph import build_graph
from relayforge.workspaces.manager import Workspaces

pytestmark = [pytest.mark.postgres, pytest.mark.docker]


def test_real_mock_coding_graph_uses_postgres_and_docker(seeded, tmp_path):
    engine, sessions, (run_id, branch_id) = seeded
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    workspaces = Workspaces(tmp_path / "workspaces")
    source = Path(__file__).resolve().parents[2] / "demo" / "sample_repo"
    workspaces.create(branch_id, source)
    tools = WorkspaceTools(workspaces)
    executor = ToolExecutor(Journal(sessions), MockExternal(tmp_path / "external.sqlite3"), tools)
    config = {"configurable": {"thread_id": run_id}}
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        graph = build_graph(executor, saver)
        final = graph.invoke(
            {"run_id": run_id, "branch_id": branch_id, "template": "fix_addition", "fault": "none"},
            config,
        )
        assert final["result"] == "SUCCEEDED"
        assert tools.result(final["test_receipt"])["exit_code"] == 0
        assert "return a + b" in workspaces.read(branch_id, "calculator.py")
    # Fresh saver/connection must read committed checkpoint state.
    with postgres_checkpointer(Settings(), schema=schema) as recovered:
        snapshot = build_graph(executor, recovered).get_state(config)
        assert snapshot.values["result"] == "SUCCEEDED"
        assert not snapshot.next
    with sessions() as session:
        assert session.get(Run, run_id).status == "SUCCEEDED"


def test_actual_sandbox_termination_never_reports_task_success(seeded, tmp_path):
    engine, sessions, (run_id, branch_id) = seeded
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.create(branch_id, Path(__file__).resolve().parents[2] / "demo" / "sample_repo")
    tools = WorkspaceTools(workspaces)
    executor = ToolExecutor(Journal(sessions), MockExternal(tmp_path / "truth.sqlite3"), tools)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        final = build_graph(executor, saver).invoke(
            {
                "run_id": run_id,
                "branch_id": branch_id,
                "template": "fix_addition",
                "fault": "sandbox_terminated",
            },
            {"configurable": {"thread_id": run_id}},
        )
        tested = tools.result(final["test_receipt"])
        assert tested["fault_injected"] and tested["exit_code"] != 0
        assert not tested["passed"] and final["result"] == "FAILED"
    with sessions() as session:
        assert session.get(Run, run_id).status == "FAILED"


def test_real_test_exit_code_survives_later_external_rejection(seeded, tmp_path):
    engine, sessions, (run_id, branch_id) = seeded
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.create(branch_id, Path(__file__).resolve().parents[2] / "demo" / "sample_repo")
    executor = ToolExecutor(
        Journal(sessions), MockExternal(tmp_path / "truth.sqlite3"), WorkspaceTools(workspaces)
    )
    with postgres_checkpointer(Settings(), schema=schema) as saver, pytest.raises(KnownNoEffect):
        build_graph(executor, saver).invoke(
            {
                "run_id": run_id,
                "branch_id": branch_id,
                "template": "fix_addition",
                "fault": "before_failure",
            },
            {"configurable": {"thread_id": run_id}},
        )
    with sessions() as session:
        run = session.get(Run, run_id)
        assert run.test_exit_code == 0
        assert run.status != "SUCCEEDED"

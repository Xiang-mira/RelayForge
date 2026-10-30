from pathlib import Path

import pytest
from sqlalchemy import select, text

from relayforge.config import Settings
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, logical_action_id
from relayforge.models import Effect, Run, Task
from relayforge.providers.mock_external import MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ApprovalRequired, OutcomeUnknown, ToolExecutor
from relayforge.runtime.forks import Forks
from relayforge.runtime.graph import build_graph
from relayforge.runtime.publication import PublicationRejected
from relayforge.workspaces.manager import Workspaces

pytestmark = [pytest.mark.postgres, pytest.mark.docker]


def setup_source(seeded, tmp_path, **provider_options):
    engine, sessions, (run_id, branch_id) = seeded
    with engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    with sessions.begin() as session:
        task_id = session.get(Run, run_id).task_id
        session.get(Task, task_id).template = "fix_addition"
    workspaces = Workspaces(tmp_path / "workspaces")
    workspaces.create(branch_id, Path(__file__).resolve().parents[2] / "demo" / "sample_repo")
    executor = ToolExecutor(
        Journal(sessions),
        MockExternal(tmp_path / "truth.sqlite3", **provider_options),
        WorkspaceTools(workspaces),
    )
    state = {
        "run_id": run_id,
        "branch_id": branch_id,
        "template": "fix_addition",
        "fault": "none",
        "snapshot_id": executor.workspace.snapshots.capture(branch_id),
    }
    return executor, state, schema


def effect_for(executor, run_id, action):
    with executor.journal.sessions() as session:
        return session.scalar(
            select(Effect).where(Effect.run_id == run_id, Effect.logical_action_id == action)
        )


def test_fork_restores_selected_snapshot_and_never_repeats_parent_effects(seeded, tmp_path):
    executor, state, schema = setup_source(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        graph = build_graph(executor, saver)
        graph.invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        forks = Forks(executor, saver)
        selected = next(row for row in forks.history(state["run_id"]) if row["stage"] == "inspect")
        child = forks.create(state["run_id"], selected["checkpoint_id"])
        with executor.journal.sessions() as session:
            child_branch = session.get(Run, child).branch_id
        assert "return a - b" in executor.workspace.workspaces.read(child_branch, "calculator.py")
        assert "return a + b" in executor.workspace.workspaces.read(
            state["branch_id"], "calculator.py"
        )
        graph.invoke(None, {"configurable": {"thread_id": child}})
        ancestor = effect_for(executor, state["run_id"], logical_action_id("publish"))
        assert executor.external.request_count(ancestor.idempotency_key) == 1
        assert executor.external.truth_count(ancestor.idempotency_key) == 1
        assert effect_for(executor, child, logical_action_id("publish")) is None
        with executor.journal.sessions() as session:
            assert session.get(Run, child).status == "SUCCEEDED"
        executor.workspace.workspaces.write(child_branch, "calculator.py", "child_only=True\n")
        assert "child_only" not in executor.workspace.workspaces.read(
            state["branch_id"], "calculator.py"
        )
        # Restore the tested version before isolating the separate payload-conflict check.
        executor.workspace.workspaces.write(
            child_branch,
            "calculator.py",
            executor.workspace.workspaces.read(state["branch_id"], "calculator.py"),
        )
        with pytest.raises(Conflict):
            executor.execute(
                child,
                child_branch,
                logical_action_id("publish"),
                "mock.create",
                {"subject": "changed"},
            )


def test_fork_does_not_inherit_approval_and_new_external_actions_require_approval(seeded, tmp_path):
    executor, state, schema = setup_source(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        build_graph(executor, saver).invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        with pytest.raises(ApprovalRequired):
            executor.execute(
                state["run_id"], state["branch_id"], "old-notify", "mock.notify", {"subject": "old"}
            )
        parent = effect_for(executor, state["run_id"], "old-notify")
        executor.approvals.decide(parent.effect_id, "APPROVED")
        executor.execute(
            state["run_id"], state["branch_id"], "old-notify", "mock.notify", {"subject": "old"}
        )
        forks = Forks(executor, saver)
        checkpoint = next(
            row for row in forks.history(state["run_id"]) if row["stage"] == "inspect"
        )
        child = forks.create(state["run_id"], checkpoint["checkpoint_id"])
        with executor.journal.sessions() as session:
            branch = session.get(Run, child).branch_id
        # New external actions now require passing branch-specific test evidence.
        build_graph(executor, saver).invoke(None, {"configurable": {"thread_id": child}})
        executor.execute(child, branch, "old-notify", "mock.notify", {"subject": "old"})
        assert executor.external.request_count(parent.idempotency_key) == 1
        for action, tool in [("new-notify", "mock.notify"), ("new-create", "mock.create")]:
            with pytest.raises(ApprovalRequired):
                executor.execute(child, branch, action, tool, {"subject": "new"})
            new = effect_for(executor, child, action)
            assert new.policy_decision == "ask" and new.approval_id != parent.approval_id
            assert not executor.approvals.authorized(new)
            assert executor.external.truth_count(new.idempotency_key) == 0


def test_unqueryable_parent_effect_is_not_guessed_or_repeated_in_fork(seeded, tmp_path):
    executor, state, schema = setup_source(seeded, tmp_path, supports_query=False)
    state["fault"] = "response_lost"
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        graph = build_graph(executor, saver)
        with pytest.raises(OutcomeUnknown):
            graph.invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        forks = Forks(executor, saver)
        checkpoint = next(
            row for row in forks.history(state["run_id"]) if row["stage"] == "inspect"
        )
        child = forks.create(state["run_id"], checkpoint["checkpoint_id"])
        with pytest.raises(PublicationRejected, match="unresolved_effect"):
            graph.invoke(None, {"configurable": {"thread_id": child}})
        ancestor = effect_for(executor, state["run_id"], logical_action_id("publish"))
        assert ancestor.status == "UNKNOWN"
        assert executor.external.request_count(ancestor.idempotency_key) == 1
        with executor.journal.sessions() as session:
            assert session.get(Run, child).status != "SUCCEEDED"


def test_pending_parent_approval_is_not_an_executed_history_reference(seeded, tmp_path):
    executor, state, schema = setup_source(seeded, tmp_path)
    with postgres_checkpointer(Settings(), schema=schema) as saver:
        build_graph(executor, saver).invoke(state, {"configurable": {"thread_id": state["run_id"]}})
        with pytest.raises(ApprovalRequired):
            executor.execute(
                state["run_id"],
                state["branch_id"],
                "pending",
                "mock.notify",
                {"subject": "pending"},
            )
        parent = effect_for(executor, state["run_id"], "pending")
        forks = Forks(executor, saver)
        checkpoint = next(
            row for row in forks.history(state["run_id"]) if row["stage"] == "inspect"
        )
        child = forks.create(state["run_id"], checkpoint["checkpoint_id"])
        with executor.journal.sessions() as session:
            branch_id = session.get(Run, child).branch_id
        build_graph(executor, saver).invoke(None, {"configurable": {"thread_id": child}})
        with pytest.raises(ApprovalRequired):
            executor.execute(child, branch_id, "pending", "mock.notify", {"subject": "pending"})
        own = effect_for(executor, child, "pending")
        assert own.approval_id != parent.approval_id
        assert executor.external.truth_count(parent.idempotency_key) == 0
        assert executor.external.truth_count(own.idempotency_key) == 0

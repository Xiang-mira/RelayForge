from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from relayforge.journal.state import JournalError, logical_action_id
from relayforge.models import Run, RunEvent
from relayforge.providers.coding import CodingProvider
from relayforge.providers.mock_coding import MockCodingProvider
from relayforge.runtime.acceptance import TaskAcceptance
from relayforge.runtime.executor import ToolExecutor
from relayforge.runtime.faults import take_fault_once
from relayforge.runtime.local_graph import LocalGraph


class CodingState(TypedDict, total=False):
    run_id: str
    branch_id: str
    template: str
    fault: str
    publish_tool: str
    stage: str
    inspect_receipt: dict
    patch_receipt: dict
    test_receipt: dict
    diff_receipt: dict
    publish_receipt: dict
    test_passed: bool
    result: str
    snapshot_id: str | None


def build_graph(executor: ToolExecutor, checkpointer, provider: CodingProvider | None = None):
    provider = provider or MockCodingProvider()
    if executor.workspace is None:
        raise ValueError("Managed workspace tools are required")
    workspace = executor.workspace
    graph = StateGraph(CodingState)

    def event(state: CodingState, node: str, data: dict | None = None):
        with executor.journal.sessions.begin() as session:
            session.add(
                RunEvent(
                    run_id=state["run_id"],
                    type="workflow.node",
                    data={"node": node, "state": "completed", **(data or {})},
                )
            )

    def call(state: CodingState, step: str, tool: str, payload: dict, fault="none"):
        return executor.execute(
            state["run_id"], state["branch_id"], logical_action_id(step), tool, payload, fault
        )

    def inspect(state: CodingState):
        receipt = call(state, "inspect", "workspace.read", {"path": "calculator.py"})
        event(state, "inspect")
        return {
            "inspect_receipt": receipt,
            "stage": "inspect",
            "snapshot_id": workspace.result(receipt).get("snapshot_id"),
        }

    def plan(state: CodingState):
        source = workspace.result(state["inspect_receipt"])["content"]
        provider.plan(state["template"], source)
        event(state, "plan")
        return {"stage": "plan"}

    def modify(state: CodingState):
        source = workspace.result(state["inspect_receipt"])["content"]
        patch = provider.patch(state["template"], source)
        receipt = call(
            state, "modify", "workspace.write", {"path": "calculator.py", "content": patch}
        )
        event(state, "modify")
        return {
            "patch_receipt": receipt,
            "stage": "modify",
            "snapshot_id": workspace.result(receipt).get("snapshot_id"),
        }

    def test(state: CodingState):
        fault = (
            take_fault_once(
                executor.journal.sessions, state["run_id"], "sandbox_terminated", node="test"
            )
            if state.get("fault") == "sandbox_terminated"
            else "none"
        )
        receipt = call(state, "test", "workspace.test", {}, fault)
        tested = workspace.result(receipt)
        with executor.journal.sessions.begin() as session:
            run = session.get(Run, state["run_id"], with_for_update=True)
            if not run or run.branch_id != state["branch_id"]:
                raise JournalError("Run/branch mismatch")
            # Preserve real tests even if a subsequent external step fails.
            run.test_exit_code = tested["exit_code"]
        event(state, "test", {"passed": tested["passed"], "exit_code": tested["exit_code"]})
        return {
            "test_receipt": receipt,
            "test_passed": bool(tested["passed"]),
            "stage": "test",
            "snapshot_id": tested.get("snapshot_id"),
        }

    def review(state: CodingState):
        source = workspace.result(state["inspect_receipt"])["content"]
        receipt = call(
            state, "review", "workspace.diff", {"path": "calculator.py", "original": source}
        )
        event(state, "review")
        return {
            "diff_receipt": receipt,
            "stage": "review",
            "snapshot_id": workspace.result(receipt).get("snapshot_id"),
        }

    def publish(state: CodingState):
        # Only the local simulated external service is enabled. No real API/model call.
        if not state["test_passed"]:
            return {"stage": "publish"}
        receipt = call(
            state,
            "publish",
            state.get("publish_tool", "mock.create"),
            {"subject": "sample-repair-result"},
            take_fault_once(executor.journal.sessions, state["run_id"], state.get("fault", "none")),
        )
        event(state, "publish")
        return {"publish_receipt": receipt, "stage": "publish"}

    def report(state: CodingState):
        result = TaskAcceptance(executor).complete(
            state["run_id"], state["branch_id"], state["test_receipt"]
        )
        return {"result": result, "stage": "report"}

    nodes = {
        "inspect": inspect,
        "plan": plan,
        "modify": modify,
        "test": test,
        "review": review,
        "publish": publish,
        "report": report,
    }
    previous = START
    for name, node in nodes.items():
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return LocalGraph(graph.compile(checkpointer=checkpointer))

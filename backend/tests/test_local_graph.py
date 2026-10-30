from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langsmith.run_helpers import get_tracing_context

from relayforge.runtime.local_graph import LocalGraph


def test_local_graph_disables_inherited_cloud_tracing(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")

    class State(TypedDict):
        observed: bool

    graph = StateGraph(State)
    graph.add_node("observe", lambda _: {"observed": bool(get_tracing_context()["enabled"])})
    graph.add_edge(START, "observe")
    graph.add_edge("observe", END)
    assert LocalGraph(graph.compile()).invoke({"observed": True})["observed"] is False

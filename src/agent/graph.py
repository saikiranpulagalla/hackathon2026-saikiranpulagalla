"""LangGraph workflow assembly — build and compile the StateGraph."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .nodes import (
    audit_close_node,
    classifier_node,
    context_fetcher_node,
    resolver_node,
    router_node,
)
from .state import AgentState


def route_edge(state: dict) -> str:
    """Conditional edge function: DLQ tickets skip resolver."""
    if state.get("routing_decision") == "dlq":
        return "audit_close"
    return "resolver"


def build_workflow() -> StateGraph:
    """
    Build and compile the 5-node LangGraph StateGraph.

    Topology:
        classifier → context_fetcher → router →[conditional]→ resolver → audit_close → END
                                                    └──────────────────→ audit_close → END
    """
    graph = StateGraph(AgentState)

    # Add all 5 nodes
    graph.add_node("classifier", classifier_node)
    graph.add_node("context_fetcher", context_fetcher_node)
    graph.add_node("router", router_node)
    graph.add_node("resolver", resolver_node)
    graph.add_node("audit_close", audit_close_node)

    # Set entry point
    graph.set_entry_point("classifier")

    # Linear edges
    graph.add_edge("classifier", "context_fetcher")
    graph.add_edge("context_fetcher", "router")

    # Conditional edge from router
    graph.add_conditional_edges(
        "router",
        route_edge,
        {"resolver": "resolver", "audit_close": "audit_close"},
    )

    # Resolver always goes to audit_close
    graph.add_edge("resolver", "audit_close")

    # Terminal edge
    graph.add_edge("audit_close", END)

    return graph.compile()

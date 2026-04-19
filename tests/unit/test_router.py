"""Unit tests for the router node."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agent.router import CONFIDENCE_THRESHOLD
from src.agent.nodes import router_node
from src.agent.state import AgentState, ErrorRecord


def _make_state(**overrides) -> AgentState:
    base = AgentState(
        ticket_id="TKT-TEST",
        ticket_text="test ticket",
        customer_id="CUST-001",
        customer_email="test@example.com",
        order_id=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        intent="refund_request",
        urgency="medium",
        resolvability="auto",
        confidence=0.85,
        classification_reasoning="test",
        order_data=None,
        customer_data=None,
        product_data=None,
        knowledge_results=None,
        context_incomplete=False,
        tool_calls=[],
        errors=[],
        node_history=[],
        retry_counts={},
        routing_decision=None,
        refund_result=None,
        resolution_status=None,
        reply_text=None,
        escalation_reason=None,
        audit_record=None,
    )
    base.update(overrides)
    return base


def test_auto_resolve_high_confidence():
    state = _make_state(confidence=0.90, resolvability="auto")
    result = router_node(state)
    assert result["routing_decision"] == "auto_resolve"
    assert "router" in result["node_history"]


def test_escalate_low_confidence():
    state = _make_state(confidence=0.40, resolvability="auto")
    result = router_node(state)
    assert result["routing_decision"] == "escalate"


def test_escalate_at_threshold_boundary():
    state = _make_state(confidence=CONFIDENCE_THRESHOLD - 0.01, resolvability="auto")
    result = router_node(state)
    assert result["routing_decision"] == "escalate"


def test_auto_resolve_at_threshold():
    state = _make_state(confidence=CONFIDENCE_THRESHOLD, resolvability="auto")
    result = router_node(state)
    assert result["routing_decision"] == "auto_resolve"


def test_escalate_human_resolvability():
    state = _make_state(confidence=0.95, resolvability="human")
    result = router_node(state)
    assert result["routing_decision"] == "escalate"


def test_dlq_on_unrecoverable_error():
    error = ErrorRecord(
        node="context_fetcher",
        tool_name="get_order",
        error_type="validation",
        message="Schema validation failed",
        timestamp=datetime.now(timezone.utc).isoformat(),
        recoverable=False,
    )
    state = _make_state(confidence=0.90, resolvability="auto", errors=[error])
    result = router_node(state)
    assert result["routing_decision"] == "dlq"


def test_node_history_appended():
    state = _make_state(confidence=0.80, resolvability="auto")
    result = router_node(state)
    assert result["node_history"] == ["router"]

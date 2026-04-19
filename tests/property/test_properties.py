"""Property-based tests using hypothesis.

Properties that must hold for all valid inputs:
1. retry_with_backoff always returns a ToolResult (never raises)
2. Routing decision is always one of the valid values
3. Confidence gate: auto_resolve only when confidence >= threshold AND resolvability == "auto"
4. DLQ routing when unrecoverable errors present
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.agent.router import CONFIDENCE_THRESHOLD
from src.agent.nodes import router_node
from src.agent.state import AgentState, ErrorRecord
from src.tools.retry import retry_with_backoff
from src.tools.schemas import OrderData


# ---------------------------------------------------------------------------
# Property 1: retry_with_backoff always returns ToolResult
# ---------------------------------------------------------------------------

@given(
    fail_count=st.integers(min_value=0, max_value=5),
    max_retries=st.integers(min_value=1, max_value=3),
)
@settings(max_examples=30, deadline=5000)
def test_retry_always_returns_result(fail_count, max_retries):
    """retry_with_backoff must never raise — always returns ToolResult."""
    from src.tools.exceptions import ToolTimeoutError

    call_count = 0

    async def flaky(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= fail_count:
            raise ToolTimeoutError("timeout")
        return {
            "order_id": "ORD-001", "customer_id": "CUST-001",
            "status": "shipped", "items": [], "total_amount": 50.0,
            "created_at": "2024-01-01T00:00:00",
        }

    result = asyncio.run(
        retry_with_backoff(flaky, args={}, schema=OrderData, tool_name="get_order", max_retries=max_retries, base_delay=0.001)
    )
    # Must always return a result, never raise
    assert result is not None
    assert isinstance(result.success, bool)
    assert result.attempt >= 1
    assert result.attempt <= max_retries


# ---------------------------------------------------------------------------
# Property 2: routing decision is always valid
# ---------------------------------------------------------------------------

@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    resolvability=st.sampled_from(["auto", "human"]),
    has_unrecoverable=st.booleans(),
)
def test_routing_decision_always_valid(confidence, resolvability, has_unrecoverable):
    """Router must always return one of the three valid decisions."""
    errors = []
    if has_unrecoverable:
        errors.append(ErrorRecord(
            node="context_fetcher",
            error_type="validation",
            message="test error",
            timestamp=datetime.now(timezone.utc).isoformat(),
            recoverable=False,
        ))

    state = AgentState(
        ticket_id="TKT-TEST",
        ticket_text="test",
        customer_id="CUST-001",
        customer_email="test@example.com",
        order_id=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        intent="refund_request",
        urgency="medium",
        resolvability=resolvability,
        confidence=confidence,
        classification_reasoning="test",
        order_data=None, customer_data=None, product_data=None, knowledge_results=None,
        context_incomplete=False,
        tool_calls=[], errors=errors, node_history=[],
        retry_counts={},
        routing_decision=None, refund_result=None,
        resolution_status=None, reply_text=None, escalation_reason=None, audit_record=None,
    )

    result = router_node(state)
    assert result["routing_decision"] in {"auto_resolve", "escalate", "dlq"}


# ---------------------------------------------------------------------------
# Property 3: confidence gate
# ---------------------------------------------------------------------------

@given(
    confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_confidence_gate(confidence):
    """auto_resolve only when confidence >= threshold AND resolvability == auto."""
    state = AgentState(
        ticket_id="TKT-TEST",
        ticket_text="test",
        customer_id="CUST-001",
        customer_email="test@example.com",
        order_id=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        intent="refund_request",
        urgency="medium",
        resolvability="auto",
        confidence=confidence,
        classification_reasoning="test",
        order_data=None, customer_data=None, product_data=None, knowledge_results=None,
        context_incomplete=False,
        tool_calls=[], errors=[], node_history=[],
        retry_counts={},
        routing_decision=None, refund_result=None,
        resolution_status=None, reply_text=None, escalation_reason=None, audit_record=None,
    )

    result = router_node(state)
    decision = result["routing_decision"]

    if decision == "auto_resolve":
        assert confidence >= CONFIDENCE_THRESHOLD
    if confidence < CONFIDENCE_THRESHOLD:
        assert decision != "auto_resolve"


# ---------------------------------------------------------------------------
# Property 4: DLQ routing on unrecoverable errors
# ---------------------------------------------------------------------------

@given(
    confidence=st.floats(min_value=0.65, max_value=1.0, allow_nan=False),
)
def test_dlq_on_unrecoverable(confidence):
    """Unrecoverable errors always route to DLQ regardless of confidence."""
    error = ErrorRecord(
        node="context_fetcher",
        error_type="validation",
        message="unrecoverable",
        timestamp=datetime.now(timezone.utc).isoformat(),
        recoverable=False,
    )
    state = AgentState(
        ticket_id="TKT-TEST",
        ticket_text="test",
        customer_id="CUST-001",
        customer_email="test@example.com",
        order_id=None,
        started_at=datetime.now(timezone.utc).isoformat(),
        intent="refund_request",
        urgency="medium",
        resolvability="auto",
        confidence=confidence,
        classification_reasoning="test",
        order_data=None, customer_data=None, product_data=None, knowledge_results=None,
        context_incomplete=False,
        tool_calls=[], errors=[error], node_history=[],
        retry_counts={},
        routing_decision=None, refund_result=None,
        resolution_status=None, reply_text=None, escalation_reason=None, audit_record=None,
    )

    result = router_node(state)
    assert result["routing_decision"] == "dlq"

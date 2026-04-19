"""Agent state definition — single source of truth passed through every LangGraph node."""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel


# --- Supporting Data Models ---

class RawTicket(BaseModel):
    """Input ticket from data/tickets.json."""
    ticket_id: str
    customer_email: str
    subject: str
    body: str
    source: str
    created_at: datetime
    tier: int
    expected_action: str


class ToolCallRecord(BaseModel):
    """Record of a single tool invocation attempt."""
    tool_name: str
    attempt: int
    timestamp: datetime
    input_args: dict
    success: bool
    error_type: Optional[str] = None
    duration_ms: float
    validated: bool = False


class ErrorRecord(BaseModel):
    """Non-fatal error encountered during processing."""
    node: str = ""
    tool_name: Optional[str] = None
    error_type: str
    message: str = ""
    timestamp: Optional[datetime] = None
    recoverable: bool = True

    def __init__(self, **data):
        if data.get("timestamp") is None:
            data["timestamp"] = datetime.now()
        super().__init__(**data)


class AuditRecord(BaseModel):
    """Complete audit trail for a single ticket."""
    ticket_id: str
    customer_id: str
    customer_email: str
    order_id: Optional[str] = None
    intent: Optional[str] = None
    urgency: Optional[str] = None
    resolvability: Optional[str] = None
    confidence: Optional[float] = None
    classification_reasoning: Optional[str] = None
    routing_decision: Optional[str] = None
    resolution_status: str
    reply_text: Optional[str] = None
    escalation_reason: Optional[str] = None
    node_history: list[str] = []
    tool_calls: list[ToolCallRecord] = []
    errors: list[ErrorRecord] = []
    total_duration_ms: float = 0.0
    started_at: datetime
    completed_at: datetime


class DLQEntry(BaseModel):
    """Dead letter queue entry for failed tickets."""
    ticket_id: str
    customer_id: str = ""
    error_type: str
    error_message: str
    node_history: list[str] = []
    tool_calls: list[ToolCallRecord] = []
    timestamp: datetime

    @classmethod
    def from_state(cls, state: dict) -> "DLQEntry":
        """Create a DLQ entry from the final agent state."""
        errors = state.get("errors", [])

        def _get_field(e, field, default=""):
            """Safely get a field from ErrorRecord or dict."""
            if isinstance(e, dict):
                return e.get(field, default)
            return getattr(e, field, default)

        error_msg = "; ".join(_get_field(e, "message", "") for e in errors[-3:]) if errors else "Unknown error"
        error_type = _get_field(errors[-1], "error_type", "unknown") if errors else "unknown"
        return cls(
            ticket_id=state.get("ticket_id", "unknown"),
            customer_id=state.get("customer_id", ""),
            error_type=error_type,
            error_message=error_msg,
            node_history=state.get("node_history", []),
            tool_calls=state.get("tool_calls", []),
            timestamp=datetime.now(),
        )

    @classmethod
    def from_exception(cls, ticket_id: str, exc: Exception) -> "DLQEntry":
        """Create a DLQ entry from an unhandled exception."""
        return cls(
            ticket_id=ticket_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            timestamp=datetime.now(),
        )


# --- Agent State TypedDict ---
# Using dict-based state for LangGraph compatibility with Annotated reducers

from typing import TypedDict


class AgentState(TypedDict):
    # --- Input ---
    ticket_id: str
    ticket_text: str
    customer_id: str
    customer_email: str
    order_id: Optional[str]

    # --- Classification ---
    intent: Optional[str]
    urgency: Optional[str]
    resolvability: Optional[str]
    confidence: Optional[float]
    classification_reasoning: Optional[str]

    # --- Context ---
    order_data: Optional[dict]
    customer_data: Optional[dict]
    product_data: Optional[dict]
    knowledge_results: Optional[list]
    context_incomplete: Optional[bool]

    # --- Execution Tracking (append-only via reducers) ---
    tool_calls: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]
    node_history: Annotated[list, operator.add]
    retry_counts: dict

    # --- Routing ---
    routing_decision: Optional[str]

    # --- Output ---
    resolution_status: Optional[str]
    reply_text: Optional[str]
    escalation_reason: Optional[str]
    refund_result: Optional[dict]
    audit_record: Optional[dict]

    # --- Timing ---
    started_at: Optional[str]

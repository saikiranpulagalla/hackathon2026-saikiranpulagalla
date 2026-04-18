"""Router logic — pure routing decisions, no LLM calls."""

from __future__ import annotations

from ..agent.state import ErrorRecord

CONFIDENCE_THRESHOLD = 0.65

URGENCY_TO_PRIORITY_MAP = {
    "high": "urgent",
    "medium": "medium",
    "low": "low",
}


def determine_routing(
    confidence: float,
    resolvability: str,
    intent: str,
    errors: list,
) -> str:
    """
    Determine routing decision based on confidence, resolvability, and errors.

    Preconditions:  0.0 <= confidence <= 1.0, resolvability in {"auto", "human"}
    Postconditions: Returns one of "auto_resolve", "escalate", "dlq"
    """
    # Check for unrecoverable errors from context fetch
    unrecoverable = [e for e in errors if hasattr(e, "recoverable") and not e.recoverable]
    if unrecoverable:
        return "dlq"

    # Human-required tickets always escalate
    if resolvability == "human":
        return "escalate"

    # Confidence gate
    if confidence >= CONFIDENCE_THRESHOLD:
        return "auto_resolve"

    return "escalate"

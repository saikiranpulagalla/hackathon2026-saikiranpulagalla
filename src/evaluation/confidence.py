"""Confidence threshold constants and routing logic.

The determine_routing() function is the single source of truth for
routing decisions. It is called by router_node() in router.py.
"""

from __future__ import annotations

from ..agent.state import ErrorRecord

CONFIDENCE_THRESHOLD = 0.65
HIGH_VALUE_REFUND_THRESHOLD = 100.0
HIGH_VALUE_CONFIDENCE_THRESHOLD = 0.80

URGENCY_TO_PRIORITY: dict[str, str] = {
    "high": "urgent",
    "medium": "medium",
    "low": "low",
}


def determine_routing(
    confidence: float,
    resolvability: str,
    intent: str,
    errors: list[ErrorRecord],
) -> str:
    """Determine routing decision.

    Preconditions:  0.0 <= confidence <= 1.0, resolvability in {"auto", "human"}
    Postconditions: Returns one of "auto_resolve", "escalate", "dlq"

    Priority order:
      1. Unrecoverable error → "dlq"
      2. resolvability == "human" → "escalate"
      3. confidence < CONFIDENCE_THRESHOLD → "escalate"
      4. else → "auto_resolve"
    """
    unrecoverable = [e for e in errors if not e.recoverable]
    if unrecoverable:
        return "dlq"
    if resolvability == "human":
        return "escalate"
    if confidence < CONFIDENCE_THRESHOLD:
        return "escalate"
    return "auto_resolve"

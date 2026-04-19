"""Router logic — pure routing decisions, no LLM calls."""

from __future__ import annotations

from ..agent.state import ErrorRecord

import os

CONFIDENCE_THRESHOLD = 0.65


def apply_demo_override(confidence: float) -> float:
    """
    If DEMO_CONFIDENCE_OVERRIDE is set (non-zero) in .env, use that confidence value.
    This lets you demonstrate the secondary gate behavior during demos.
    Only active when explicitly set in .env — never active in production.

    NOTE: We read the env var at call time (not module import time) so that
    dotenv loading in Streamlit / live_tab.py takes effect correctly.
    """
    try:
        override = float(os.getenv("DEMO_CONFIDENCE_OVERRIDE", "0"))
    except (ValueError, TypeError):
        override = 0.0
    if override > 0:
        return override
    return confidence

URGENCY_TO_PRIORITY_MAP = {
    "high": "urgent",
    "medium": "medium",
    "low": "low",
}

# Intents that always require human review regardless of confidence
ALWAYS_ESCALATE_INTENTS = {
    "technical_support",
    "billing_dispute", 
    "legal_threat",
    "account_security",
    "complaint",       # high-urgency complaints need human judgment
}

# Intents that can be auto-resolved if confidence is sufficient  
AUTO_RESOLVABLE_INTENTS = {
    "refund_request",
    "order_status", 
    "product_inquiry",
    "shipping_inquiry",
    "other",
    "unknown",
}

def determine_routing(
    confidence: float,
    resolvability: str,
    intent: str,
    errors: list,
    urgency: str = "low",
    context_incomplete: bool = False
) -> str:
    """
    Determine routing decision based on confidence, resolvability, and errors.

    Preconditions:  0.0 <= confidence <= 1.0, resolvability in {"auto", "human"}
    Postconditions: Returns one of "auto_resolve", "escalate", "dlq"
    """
    # Rule 1: Check for unrecoverable errors from context fetch
    unrecoverable = [e for e in errors if hasattr(e, "recoverable") and not e.recoverable]
    if unrecoverable:
        return "dlq"

    confidence = apply_demo_override(confidence)

    # Rule 2: Always-escalate intents → escalate regardless of confidence
    if intent in ALWAYS_ESCALATE_INTENTS:
        return "escalate"

    # Rule 3: Human-required tickets always escalate
    if resolvability == "human":
        return "escalate"

    # Rule 3b: Incomplete context for high-urgency or complaint tickets → escalate
    if context_incomplete and urgency in ["high", "urgent"]:
        return "escalate"

    # Rule 4: Primary confidence gate
    if confidence < CONFIDENCE_THRESHOLD:
        return "escalate"

    # Rule 5: Secondary gate — high urgency with moderate confidence
    if urgency == "high" and confidence < 0.80:
        return "escalate"

    # Rule 6: Auto-resolvable intents above confidence threshold
    return "auto_resolve"

"""Mock tool implementations simulating e-commerce backend APIs."""

from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel

from .exceptions import ToolError, ToolTimeoutError


# --- Failure Rate Configuration ---

class ToolFailureConfig(BaseModel):
    timeout_rate: float    # probability of raising ToolTimeoutError
    malformed_rate: float  # probability of returning structurally invalid data
    error_rate: float      # probability of raising ToolError


TOOL_FAILURE_CONFIG: dict[str, ToolFailureConfig] = {
    "get_order":                ToolFailureConfig(timeout_rate=0.10, malformed_rate=0.05, error_rate=0.05),
    "get_customer":             ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.03, error_rate=0.02),
    "check_refund_eligibility": ToolFailureConfig(timeout_rate=0.15, malformed_rate=0.08, error_rate=0.07),
    "issue_refund":             ToolFailureConfig(timeout_rate=0.10, malformed_rate=0.05, error_rate=0.10),
    "search_knowledge_base":    ToolFailureConfig(timeout_rate=0.08, malformed_rate=0.10, error_rate=0.05),
    "get_product":              ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.03),
    "send_reply":               ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.02, error_rate=0.03),
    "escalate":                 ToolFailureConfig(timeout_rate=0.03, malformed_rate=0.02, error_rate=0.02),
}


# --- Failure Simulation Helpers ---

def _maybe_raise(tool_name: str) -> None:
    """Raises ToolTimeoutError or ToolError based on configured rates."""
    config = TOOL_FAILURE_CONFIG[tool_name]
    roll = random.random()
    if roll < config.timeout_rate:
        raise ToolTimeoutError(f"{tool_name} timed out")
    if roll < config.timeout_rate + config.error_rate:
        raise ToolError(f"{tool_name} returned server error", is_transient=True)


def _should_malform(tool_name: str) -> bool:
    """Returns True if this call should return a malformed dict."""
    config = TOOL_FAILURE_CONFIG[tool_name]
    return random.random() < config.malformed_rate


async def _simulate_latency(min_ms: int, max_ms: int) -> None:
    """Simulate realistic API latency."""
    delay = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay)


# --- Fake Data Generators ---

_FIRST_NAMES = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Hank", "Iris", "Jack"]
_LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Moore"]
_PRODUCT_NAMES = [
    "Wireless Earbuds Pro", "USB-C Hub 7-in-1", "Ergonomic Keyboard", "4K Webcam",
    "Portable Charger 20000mAh", "Smart Watch Series 5", "Noise Cancelling Headphones",
    "Mechanical Keyboard RGB", "Laptop Stand Adjustable", "Bluetooth Speaker Waterproof",
]
_KB_TITLES = {
    "refund": "How to Process Refund Requests",
    "return": "Return Policy and Procedures",
    "shipping": "Shipping Times and Tracking Info",
    "order": "Order Status Guide",
    "product": "Product Specifications and FAQs",
    "billing": "Billing and Payment Issues",
    "account": "Account Management Help",
    "default": "General Customer Support Guide",
}


def _derive_customer_id(email: str) -> str:
    """Deterministically derive a customer ID from email."""
    h = hashlib.md5(email.encode()).hexdigest()[:6]
    return f"CUST-{h.upper()}"


def _fake_customer_id(order_id: str) -> str:
    h = hashlib.md5(order_id.encode()).hexdigest()[:6]
    return f"CUST-{h.upper()}"


def _fake_name() -> str:
    return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"


def _fake_order_item() -> dict:
    return {
        "product_id": f"PROD-{random.randint(100, 999)}",
        "name": random.choice(_PRODUCT_NAMES),
        "quantity": random.randint(1, 3),
        "unit_price": round(random.uniform(10.0, 200.0), 2),
    }


def _fake_past_datetime(days: int = 60) -> datetime:
    return datetime.now() - timedelta(days=random.randint(1, days))


def _fake_tracking() -> str:
    return f"TRK-{uuid4().hex[:12].upper()}"


def _fake_kb_title(query: str) -> str:
    query_lower = query.lower()
    for keyword, title in _KB_TITLES.items():
        if keyword in query_lower:
            return title
    return _KB_TITLES["default"]


def _fake_kb_content() -> str:
    paragraphs = [
        "Our standard return policy allows returns within 30 days of delivery for most items.",
        "For refund requests, please ensure the item is in its original packaging.",
        "Shipping typically takes 3-7 business days for standard delivery.",
        "Premium and VIP customers receive priority support and faster resolution.",
        "If you are experiencing billing issues, please verify your payment method first.",
        "Account-related issues can usually be resolved by resetting your password.",
    ]
    return " ".join(random.sample(paragraphs, k=min(3, len(paragraphs))))


def _fake_product_name() -> str:
    return random.choice(_PRODUCT_NAMES)


def _fake_product_description() -> str:
    descriptions = [
        "High-quality product designed for everyday use with premium materials.",
        "Top-rated item featuring the latest technology and ergonomic design.",
        "Best-selling product with excellent customer reviews and durability.",
    ]
    return random.choice(descriptions)


# --- Tool Implementations ---

async def get_order(order_id: str) -> dict:
    """
    Fetch order details by order ID.
    Preconditions:  order_id is a non-empty string
    Postconditions: Returns dict matching OrderData schema on success
    Failure modes:  ToolTimeoutError (10%), malformed dict returned (5%), ToolError (5%)
    """
    await _simulate_latency(50, 200)
    _maybe_raise("get_order")
    if _should_malform("get_order"):
        return {"order_id": order_id, "status": None, "items": "INVALID"}
    return {
        "order_id": order_id,
        "customer_id": _fake_customer_id(order_id),
        "status": random.choice(["pending", "shipped", "delivered", "cancelled"]),
        "items": [_fake_order_item() for _ in range(random.randint(1, 4))],
        "total_amount": round(random.uniform(10.0, 500.0), 2),
        "created_at": _fake_past_datetime().isoformat(),
        "tracking_number": _fake_tracking() if random.random() > 0.3 else None,
    }


async def get_customer(email: str) -> dict:
    """
    Fetch customer details by email (primary lookup key per hackathon spec).
    Preconditions:  email is a non-empty string
    Postconditions: Returns dict matching CustomerData schema on success
    Failure modes:  ToolTimeoutError (5%), malformed dict returned (3%), ToolError (2%)
    """
    await _simulate_latency(30, 100)
    _maybe_raise("get_customer")
    if _should_malform("get_customer"):
        return {"customer_id": "INVALID", "name": None}
    return {
        "customer_id": _derive_customer_id(email),
        "name": _fake_name(),
        "email": email,
        "tier": random.choice(["standard", "premium", "vip"]),
        "total_orders": random.randint(1, 50),
        "account_created": _fake_past_datetime(days=730).isoformat(),
    }


async def check_refund_eligibility(order_id: str) -> dict:
    """
    Check if an order is eligible for a refund.
    Preconditions:  order_id is a non-empty string
    Postconditions: Returns dict matching RefundEligibilityData schema on success
    Failure modes:  ToolTimeoutError (15%), malformed dict returned (8%), ToolError (7%)
    """
    await _simulate_latency(100, 400)
    _maybe_raise("check_refund_eligibility")
    if _should_malform("check_refund_eligibility"):
        return {"eligible": "yes", "reason": None}
    eligible = random.random() > 0.3
    return {
        "eligible": eligible,
        "reason": "Within 30-day return window" if eligible else "Outside return window",
        "max_refund_amount": round(random.uniform(10.0, 200.0), 2) if eligible else None,
        "policy_reference": "POLICY-2024-RETURNS-v3",
    }


async def issue_refund(order_id: str, amount: float) -> dict:
    """
    Issue a refund for an order. IRREVERSIBLE — caller must check eligibility first.
    Preconditions:  order_id non-empty, amount is a positive number
    Postconditions: Returns dict matching RefundResult schema on success
    Failure modes:  ToolTimeoutError (10%), malformed dict returned (5%), ToolError (10%)
    """
    await _simulate_latency(200, 800)
    _maybe_raise("issue_refund")
    if _should_malform("issue_refund"):
        return {"refund_id": None, "amount": "not_a_number"}
    return {
        "refund_id": f"REF-{uuid4().hex[:8].upper()}",
        "amount": amount,
        "status": random.choice(["approved", "pending"]),
        "estimated_days": random.randint(3, 10),
    }


async def search_knowledge_base(query: str, max_results: int = 3) -> list[dict]:
    """
    Search the knowledge base for relevant articles.
    Preconditions:  query is a non-empty string, 1 <= max_results <= 10
    Postconditions: Returns list of dicts matching KnowledgeResult schema on success
    Failure modes:  ToolTimeoutError (8%), malformed list returned (10%), ToolError (5%)
    """
    await _simulate_latency(80, 300)
    _maybe_raise("search_knowledge_base")
    if _should_malform("search_knowledge_base"):
        return [{"article_id": None, "title": 123}]
    return [
        {
            "article_id": f"KB-{random.randint(1000, 9999)}",
            "title": _fake_kb_title(query),
            "content": _fake_kb_content(),
            "relevance_score": round(random.uniform(0.5, 1.0), 3),
            "category": random.choice(["shipping", "returns", "billing", "product", "account"]),
        }
        for _ in range(min(max_results, random.randint(1, max_results)))
    ]


async def get_product(product_id: str) -> dict:
    """
    Fetch product details by product ID.
    Preconditions:  product_id is a non-empty string
    Postconditions: Returns dict matching ProductData schema on success
    Failure modes:  ToolTimeoutError (5%), malformed dict returned (5%), ToolError (3%)
    """
    await _simulate_latency(40, 150)
    _maybe_raise("get_product")
    if _should_malform("get_product"):
        return {"product_id": product_id, "price": "free", "in_stock": "maybe"}
    return {
        "product_id": product_id,
        "name": _fake_product_name(),
        "description": _fake_product_description(),
        "price": round(random.uniform(5.0, 300.0), 2),
        "in_stock": random.random() > 0.2,
        "category": random.choice(["electronics", "clothing", "home", "sports", "books"]),
    }


async def send_reply(ticket_id: str, message: str) -> dict:
    """
    Send a reply message to the customer.
    Preconditions:  ticket_id and message are non-empty strings, message <= 2000 chars
    Postconditions: Returns dict matching SendReplyResult schema on success
    Failure modes:  ToolTimeoutError (5%), malformed dict returned (2%), ToolError (3%)
    """
    await _simulate_latency(50, 200)
    _maybe_raise("send_reply")
    if _should_malform("send_reply"):
        return {"message_id": None, "delivered": "yes"}
    return {
        "message_id": f"MSG-{uuid4().hex[:10].upper()}",
        "delivered": True,
        "channel": random.choice(["email", "chat"]),
    }


async def escalate(ticket_id: str, summary: str, priority: str = "medium") -> dict:
    """
    Escalate a ticket to a human agent team.
    Preconditions:  ticket_id and summary non-empty, priority in valid set
    Postconditions: Returns dict matching EscalationResult schema on success
    Failure modes:  ToolTimeoutError (3%), malformed dict returned (2%), ToolError (2%)
    """
    await _simulate_latency(30, 100)
    _maybe_raise("escalate")
    if _should_malform("escalate"):
        return {"escalation_id": None, "priority": "unknown"}
    return {
        "escalation_id": f"ESC-{uuid4().hex[:8].upper()}",
        "assigned_team": random.choice(["tier2_support", "billing_team", "technical_team"]),
        "priority": priority,
        "estimated_response_hours": random.randint(2, 24),
    }


# --- Tool Registry ---

TOOL_REGISTRY: dict[str, callable] = {
    "get_order": get_order,
    "get_customer": get_customer,
    "get_product": get_product,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "search_knowledge_base": search_knowledge_base,
    "send_reply": send_reply,
    "escalate": escalate,
}

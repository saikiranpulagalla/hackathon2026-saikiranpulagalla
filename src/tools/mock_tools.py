"""Mock tool implementations using official hackathon sample data."""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from .exceptions import ToolError, ToolTimeoutError


# --- Failure Rate Configuration ---

class ToolFailureConfig(BaseModel):
    timeout_rate: float
    malformed_rate: float
    error_rate: float


TOOL_FAILURE_CONFIG: dict[str, ToolFailureConfig] = {
    "get_order":                ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.05),
    "get_customer":             ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.03, error_rate=0.02),
    "get_customer_orders":      ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.03, error_rate=0.02),
    "check_refund_eligibility": ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.05),
    "issue_refund":             ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.10),
    "search_knowledge_base":    ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.05),
    "get_product":              ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.05, error_rate=0.03),
    "send_reply":               ToolFailureConfig(timeout_rate=0.05, malformed_rate=0.02, error_rate=0.03),
    "escalate":                 ToolFailureConfig(timeout_rate=0.03, malformed_rate=0.02, error_rate=0.02),
}


# --- Failure Simulation Helpers ---

def _maybe_raise(tool_name: str) -> None:
    config = TOOL_FAILURE_CONFIG.get(tool_name)
    if not config:
        return
    roll = random.random()
    if roll < config.timeout_rate:
        raise ToolTimeoutError(f"{tool_name} timed out")
    if roll < config.timeout_rate + config.error_rate:
        raise ToolError(f"{tool_name} returned server error", is_transient=True)


def _should_malform(tool_name: str) -> bool:
    config = TOOL_FAILURE_CONFIG.get(tool_name)
    if not config:
        return False
    return random.random() < config.malformed_rate


async def _simulate_latency(min_ms: int, max_ms: int) -> None:
    delay = random.uniform(min_ms / 1000, max_ms / 1000)
    await asyncio.sleep(delay)


# --- Data Loading ---

DATA_DIR = Path(__file__).parent.parent.parent / "data"


def _load_json(filename: str) -> list | dict:
    path = DATA_DIR / filename
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


CUSTOMERS: list[dict] = _load_json("customers.json")
ORDERS: list[dict] = _load_json("orders.json")
PRODUCTS: list[dict] = _load_json("products.json")
TICKETS: list[dict] = _load_json("tickets.json")


def _load_kb() -> list[dict]:
    path = DATA_DIR / "knowledgebase.md"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    sections = re.split(r'\n## ', content)
    kb = []
    for i, section in enumerate(sections):
        if not section.strip():
            continue
        lines = section.split('\n')
        title = lines[0].strip().replace("# ", "")
        body = '\n'.join(lines[1:]).strip()
        kb.append({
            "article_id": f"KB-{i}",
            "title": title,
            "content": body,
            "relevance_score": 1.0,
            "category": "policy"
        })
    return kb


KNOWLEDGE_BASE = _load_kb()

# Derive a simulation reference date from the latest ticket
_SIMULATION_TODAY = datetime(2024, 3, 22)  # latest ticket date
for _t in TICKETS:
    try:
        _dt = datetime.fromisoformat(_t["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        if _dt > _SIMULATION_TODAY:
            _SIMULATION_TODAY = _dt
    except Exception:
        pass


# --- Tool Implementations ---

async def get_order(order_id: str) -> dict:
    """Fetch order details by order ID."""
    await _simulate_latency(50, 200)
    _maybe_raise("get_order")
    if _should_malform("get_order"):
        return {"order_id": order_id, "status": None}

    for order in ORDERS:
        if order["order_id"] == order_id:
            return order
    raise ToolError(f"Order {order_id} not found", is_transient=False)


async def get_customer(email: str) -> dict:
    """Fetch customer details by email."""
    await _simulate_latency(30, 100)
    _maybe_raise("get_customer")
    if _should_malform("get_customer"):
        return {"customer_id": "INVALID", "name": None}

    for customer in CUSTOMERS:
        if customer["email"].lower() == email.lower():
            return customer
    raise ToolError(f"Customer with email {email} not found", is_transient=False)


async def get_customer_orders(customer_id: str) -> list[dict]:
    """Fetch all orders for a customer by customer_id."""
    await _simulate_latency(50, 200)
    _maybe_raise("get_customer_orders")

    results = [o for o in ORDERS if o["customer_id"] == customer_id]
    if not results:
        # Try by email match through customer lookup
        for c in CUSTOMERS:
            if c["customer_id"] == customer_id:
                break
        raise ToolError(f"No orders found for customer {customer_id}", is_transient=False)
    return results


async def check_refund_eligibility(order_id: str) -> dict:
    """
    Check if an order is eligible for a refund.
    Uses order notes, return_deadline, refund_status, and product data to determine eligibility.
    """
    await _simulate_latency(100, 400)
    _maybe_raise("check_refund_eligibility")
    if _should_malform("check_refund_eligibility"):
        return {"eligible": "yes", "reason": None}

    order = next((o for o in ORDERS if o["order_id"] == order_id), None)
    if not order:
        return {
            "eligible": False,
            "reason": f"Order {order_id} not found in our system.",
            "policy_reference": "N/A"
        }

    notes = str(order.get("notes", "")).lower()

    # 1. Already refunded
    if order.get("refund_status") == "refunded":
        return {
            "eligible": False,
            "reason": "Refund already processed for this order.",
            "max_refund_amount": None,
            "policy_reference": "Refund Policy: Refunds cannot be re-issued once processed."
        }

    # 2. Order still processing — can cancel
    if order.get("status") == "processing":
        return {
            "eligible": True,
            "reason": "Order has not shipped yet. Full cancellation and refund is allowed.",
            "max_refund_amount": order.get("amount"),
            "policy_reference": "Order Cancellation Policy"
        }

    # 3. Non-returnable per policy (e.g., device registered online)
    if "non-returnable" in notes:
        return {
            "eligible": False,
            "reason": "This item is non-returnable per our policy. " + order.get("notes", ""),
            "max_refund_amount": None,
            "policy_reference": "Product Return Restrictions"
        }

    # 4. VIP pre-approved exception
    if "pre-approved" in notes or "exception" in notes:
        return {
            "eligible": True,
            "reason": "VIP customer with pre-approved extended return exception on file.",
            "max_refund_amount": order.get("amount"),
            "policy_reference": "VIP Customer Exceptions Policy"
        }

    # 5. Check return deadline
    deadline_str = order.get("return_deadline")
    if deadline_str:
        try:
            deadline = datetime.strptime(deadline_str, "%Y-%m-%d")
            if deadline < _SIMULATION_TODAY:
                # Window expired — check if warranty still applies
                if "warranty" in notes and "active" in notes:
                    return {
                        "eligible": False,
                        "reason": f"Return window expired on {deadline_str}. However, warranty is still active — this should be handled as a warranty claim, not a refund.",
                        "max_refund_amount": None,
                        "policy_reference": "Warranty Claims Policy"
                    }
                return {
                    "eligible": False,
                    "reason": f"Return window expired on {deadline_str}.",
                    "max_refund_amount": None,
                    "policy_reference": "Standard Return Window Policy"
                }
        except ValueError:
            pass

    # 6. Within return window or no deadline set
    return {
        "eligible": True,
        "reason": "Order is within the standard return window.",
        "max_refund_amount": order.get("amount"),
        "policy_reference": "Standard Return Window Policy"
    }


async def issue_refund(order_id: str, amount: float) -> dict:
    """Issue a refund for an order. IRREVERSIBLE."""
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
    """Search the knowledge base for relevant articles."""
    await _simulate_latency(80, 300)
    _maybe_raise("search_knowledge_base")
    if _should_malform("search_knowledge_base"):
        return [{"article_id": None, "title": 123}]

    query_lower = query.lower()
    query_words = set(query_lower.split())

    scored = []
    for article in KNOWLEDGE_BASE:
        title_lower = article["title"].lower()
        content_lower = article["content"].lower()
        score = 0
        for word in query_words:
            if word in title_lower:
                score += 3
            if word in content_lower:
                score += 1
        if score > 0:
            scored.append((score, article))

    scored.sort(key=lambda x: -x[0])
    results = [a for _, a in scored[:max_results]]

    if not results:
        return KNOWLEDGE_BASE[:max_results]

    return results


async def get_product(product_id: str) -> dict:
    """Fetch product details by product ID."""
    await _simulate_latency(40, 150)
    _maybe_raise("get_product")
    if _should_malform("get_product"):
        return {"product_id": product_id, "price": "free", "in_stock": "maybe"}

    for product in PRODUCTS:
        if product["product_id"] == product_id:
            return product
    raise ToolError(f"Product {product_id} not found", is_transient=False)


async def send_reply(ticket_id: str, message: str) -> dict:
    """Send a reply message to the customer."""
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
    """Escalate a ticket to a human agent team."""
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
    "get_customer_orders": get_customer_orders,
    "get_product": get_product,
    "check_refund_eligibility": check_refund_eligibility,
    "issue_refund": issue_refund,
    "search_knowledge_base": search_knowledge_base,
    "send_reply": send_reply,
    "escalate": escalate,
}

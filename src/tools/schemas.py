"""Pydantic v2 response schemas for all mock tool outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


# --- Order ---

class OrderItem(BaseModel):
    product_id: str
    name: str
    quantity: int
    unit_price: float


class OrderData(BaseModel):
    """Schema for get_order() responses."""
    order_id: str
    customer_id: str
    status: Literal["pending", "shipped", "delivered", "cancelled", "returned"]
    items: list[OrderItem]
    total_amount: float
    created_at: datetime
    tracking_number: Optional[str] = None

    model_config = ConfigDict(strict=False)


# --- Customer ---

class CustomerData(BaseModel):
    """Schema for get_customer() responses."""
    customer_id: str
    name: str
    email: str
    tier: Literal["standard", "premium", "vip"]
    total_orders: int
    account_created: datetime

    model_config = ConfigDict(strict=False)  # mock tools return datetime as ISO strings


# --- Product ---

class ProductData(BaseModel):
    """Schema for get_product() responses."""
    product_id: str
    name: str
    description: str
    price: float
    in_stock: bool
    category: str

    model_config = ConfigDict(strict=False)


# --- Refund ---

class RefundEligibilityData(BaseModel):
    """Schema for check_refund_eligibility() responses."""
    eligible: bool
    reason: str
    max_refund_amount: Optional[float] = None
    policy_reference: str

    model_config = ConfigDict(strict=False)


class RefundResult(BaseModel):
    """Schema for issue_refund() responses."""
    refund_id: str
    amount: float
    status: Literal["approved", "pending", "rejected"]
    estimated_days: int

    model_config = ConfigDict(strict=False)


# --- Knowledge Base ---

class KnowledgeResult(BaseModel):
    """Schema for search_knowledge_base() responses."""
    article_id: str
    title: str
    content: str
    relevance_score: float
    category: str

    model_config = ConfigDict(strict=False)


# --- Communication ---

class SendReplyResult(BaseModel):
    """Schema for send_reply() responses."""
    message_id: str
    delivered: bool
    channel: Literal["email", "chat", "sms"]

    model_config = ConfigDict(strict=True)


class EscalationResult(BaseModel):
    """Schema for escalate() responses."""
    escalation_id: str
    assigned_team: str
    priority: Literal["low", "medium", "high", "urgent"]
    estimated_response_hours: int

    model_config = ConfigDict(strict=True)


# --- Schema Registry ---

SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "get_order": OrderData,
    "get_customer": CustomerData,
    "get_product": ProductData,
    "check_refund_eligibility": RefundEligibilityData,
    "issue_refund": RefundResult,
    "search_knowledge_base": KnowledgeResult,
    "send_reply": SendReplyResult,
    "escalate": EscalationResult,
}

"""Pydantic v2 response schemas for all mock tool outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


# --- Order ---

class OrderData(BaseModel):
    """Schema for get_order() responses."""
    order_id: str
    customer_id: str
    product_id: str
    quantity: int
    amount: float
    status: Literal["processing", "shipped", "delivered", "cancelled", "returned"]
    order_date: str
    delivery_date: Optional[str] = None
    return_deadline: Optional[str] = None
    refund_status: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(strict=False)


# --- Customer ---

class Address(BaseModel):
    street: str
    city: str
    state: str
    zip: str

class CustomerData(BaseModel):
    """Schema for get_customer() responses."""
    customer_id: str
    name: str
    email: str
    phone: str
    tier: Literal["standard", "premium", "vip"]
    member_since: str
    total_orders: int
    total_spent: float
    address: Address
    notes: Optional[str] = None

    model_config = ConfigDict(strict=False)


# --- Product ---

class ProductData(BaseModel):
    """Schema for get_product() responses."""
    product_id: str
    name: str
    category: str
    price: float
    warranty_months: int
    return_window_days: int
    returnable: bool
    notes: Optional[str] = None

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

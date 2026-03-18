"""Order Agent Pydantic models and LangGraph state."""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class OrderAgentState(TypedDict, total=False):
    """LangGraph state for Order Agent (per data-model.md, T038).

    A2ATaskPayload fields + agent-specific processing fields.
    Serialized to/from Redis via AsyncRedisSaver (thread_id=task_id, TTL=1h).
    """

    # A2ATaskPayload identity fields
    task_id: str
    plan_id: str
    original_message: str
    instructions: str
    conversation_history: list
    dependency_results: dict

    # Agent-specific processing fields
    memory_context: str
    entities: dict  # OrderEntities serialized as dict
    matched_products: list  # list[ProductMatch] serialized as dicts
    customer: dict  # {customer_id, customer_name, not_found}
    order_preview: dict  # {items, total_estimate, ready, confirm_message}
    confirm_message: str  # presented to user before interrupt
    user_confirmation: str  # populated by Command(resume={"user_confirmation": ...})
    result: dict  # final A2AResult output


# ---------------------------------------------------------------------------
# Extracted entities (NLP output)
# ---------------------------------------------------------------------------


class OrderItem(BaseModel):
    """A single item extracted from natural language before product matching."""

    product_query: str
    quantity: int = Field(default=1, ge=1)
    note: str | None = None


class OrderEntities(BaseModel):
    """NLP extraction output from a raw order message."""

    customer_name: str | None = None
    customer_honorific: str | None = None
    table_number: str | None = None
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    intent_modifier: Literal["new", "add", "remove", "cancel"] = "new"


# ---------------------------------------------------------------------------
# Product matching
# ---------------------------------------------------------------------------


class ProductMatch(BaseModel):
    """Result of matching an OrderItem against the FAISS product index."""

    product_query: str
    matched_product_id: str | None = None
    matched_product_name: str | None = None
    price: float | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)
    match_status: Literal["auto", "rerank", "ask_user", "not_found"] = "not_found"
    candidates: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Order draft
# ---------------------------------------------------------------------------


class ResolvedOrderItem(BaseModel):
    """A matched and confirmed order item ready for submission."""

    product_id: str
    product_name: str
    quantity: int = Field(ge=1)
    price: float
    note: str | None = None
    variant: str | None = None


class OrderDraft(BaseModel):
    """In-progress order held during multi-turn confirmation flow."""

    draft_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    customer_id: str | None = None
    customer_name: str | None = None
    table_number: str | None = None
    items: list[ResolvedOrderItem] = Field(default_factory=list)
    discount: float = 0.0
    notes: str | None = None
    total_estimate: float | None = None
    status: Literal["building", "awaiting_confirm", "confirmed", "submitted", "cancelled"] = "building"

    def calculate_total(self) -> float:
        return sum(item.price * item.quantity for item in self.items) * (1 - self.discount)

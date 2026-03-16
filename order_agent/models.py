"""Order Agent Pydantic models and LangGraph state."""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class OrderAgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    message: str
    intent_modifier: str  # new | add | remove | cancel
    entities: dict[str, Any] | None
    product_matches: list[dict[str, Any]]
    customer_id: str | None
    customer_name: str | None
    order_draft: dict[str, Any] | None
    confirmation: str | None  # user's confirmation input
    order_result: dict[str, Any] | None
    requires_input: bool
    input_request: str
    status: str  # building | awaiting_confirm | confirmed | submitted | cancelled | error
    error: str | None
    reasoning_summary: str
    tool_calls: list[str]


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

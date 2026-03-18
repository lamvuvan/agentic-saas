"""Customer Agent Pydantic models and LangGraph state."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class CustomerAgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    message: str
    skill: str  # lookup_customer | create_customer | update_customer
    search_query: str | None
    customer_id: str | None
    customers: list[dict[str, Any]]
    action: str | None  # lookup | create | update
    customer_data: dict[str, Any] | None  # payload for create/update
    memory_hit: bool
    requires_input: bool
    input_request: str
    status: str  # working | awaiting_confirm | completed | failed
    error: str | None
    reasoning_summary: str
    tool_calls: list[str]


# ---------------------------------------------------------------------------
# Customer record
# ---------------------------------------------------------------------------


class CustomerRecord(BaseModel):
    """A customer record returned by Tool Registry customer__get_customers."""

    customer_id: str
    name: str
    phone: str | None = None
    address: str | None = None
    code: str | None = None
    email: str | None = None


# ---------------------------------------------------------------------------
# Lookup result
# ---------------------------------------------------------------------------


class CustomerLookupResult(BaseModel):
    """Output of the Customer Agent after a lookup, create, or update skill."""

    skill: Literal["lookup_customer", "create_customer", "update_customer"]
    customers: list[CustomerRecord] = Field(default_factory=list)
    customer_id: str | None = None
    action: Literal["lookup", "create", "update"] | None = None
    memory_hit: bool = False
    message: str = ""
    status: str = "completed"

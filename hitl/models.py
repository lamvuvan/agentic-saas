"""Human-in-the-Loop (HITL) data models — deepagents native HITL."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HITLStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class ActionRequest(BaseModel):
    """A single pending tool call awaiting human decision."""
    name: str = Field(..., description="Tool name")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")


class ConfirmRequest(BaseModel):
    """User confirms or rejects pending actions.

    confirm_token  : session_id (used as LangGraph thread_id for resume)
    decisions      : list matching the action_requests order
                     each item: {"type": "approve"} | {"type": "reject"} |
                                {"type": "edit", "edited_action": {"name":..,"args":..}}
    """
    confirm_token: str = Field(..., description="session_id / LangGraph thread_id")
    decisions: list[dict[str, Any]] = Field(
        ...,
        description='List of decisions: [{type: "approve"|"edit"|"reject", ...}]',
    )


class ConfirmResponse(BaseModel):
    """Response after confirming/rejecting."""
    confirm_token: str
    status: HITLStatus
    response: str | None = Field(None, description="Final agent response after resume")
    message: str = ""

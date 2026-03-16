"""BI Agent Pydantic models and LangGraph state."""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, Field


class BIAgentState(TypedDict, total=False):
    task_id: str
    session_id: str
    nl_input: str
    schema_context: str
    generated_sql: str | None
    safety_passed: bool
    safety_reason: str | None
    rows: list[dict[str, Any]]
    row_count: int
    limit_applied: bool
    formatted_summary: str
    status: str  # success | rejected | error
    error: str | None
    reasoning_summary: str
    tool_calls: list[str]


class BIQueryResult(BaseModel):
    query_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nl_input: str
    generated_sql: str
    row_count: int = 0
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    formatted_summary: str
    limit_applied: bool = False
    safety_passed: bool = True
    status: Literal["success", "rejected", "error"] = "success"
    error_reason: str | None = None

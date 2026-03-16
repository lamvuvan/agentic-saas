"""Orchestrator Pydantic models and LangGraph state."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------


class OrchestratorState(TypedDict, total=False):
    """LangGraph StateGraph state for the Orchestrator graph."""

    message: str
    session_id: str
    trace_id: str
    history: list[dict[str, str]]  # Last 3 conversation turns
    intent: str
    confidence: float
    escalated: bool
    model_used: str
    plan: dict[str, Any] | None  # ExecutionPlan serialized
    agent_results: list[dict[str, Any]]
    reply: str
    requires_input: bool
    replan_count: int
    error: str | None


# ---------------------------------------------------------------------------
# Intent classification output
# ---------------------------------------------------------------------------


class IntentResult(BaseModel):
    intent: str = Field(
        description="One of: order, bi_query, chitchat, unknown"
    )
    confidence: float = Field(ge=0.0, le=1.0)
    entities: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


# ---------------------------------------------------------------------------
# HTTP request / response
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: str
    trace_id: str
    requires_input: bool = False
    metadata: dict[str, Any] | None = None



"""Orchestrator Pydantic models and LangGraph state."""

from __future__ import annotations

from datetime import datetime
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
    plan_id: str | None  # PostgreSQL DisplayPlan id (None if plan persistence unavailable)
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


# ---------------------------------------------------------------------------
# Plan visibility models (DisplayPlan + PlanSubGoal)
# ---------------------------------------------------------------------------


class PlanSubGoalResponse(BaseModel):
    """A single step within a DisplayPlan — returned by GET /plans/* endpoints."""

    id: str | None = None
    plan_id: str | None = None
    sequence: int
    title: str
    agent_name: str  # internal routing identifier (e.g. "bi-agent")
    agent_label: str  # user-visible display label (e.g. "Báo Cáo & Phân Tích")
    a2a_task_id: str | None = None
    status: str = "pending"  # pending | running | completed | failed
    result_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DisplayPlanResponse(BaseModel):
    """User-visible plan stored in PostgreSQL — returned by GET /plans/* endpoints."""

    id: str
    session_id: str
    tenant_id: str | None = None
    goal: str
    status: str = "running"  # pending | running | completed | failed
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PlanDetailResponse(BaseModel):
    """Full response for GET /plans/* endpoints."""

    plan: DisplayPlanResponse
    sub_goals: list[PlanSubGoalResponse]



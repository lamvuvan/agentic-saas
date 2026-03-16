"""Shared A2A protocol models — used by Orchestrator and all Domain Agents."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    INPUT_REQUIRED = "input-required"


class A2AResult(BaseModel):
    """Structured result returned by a Domain Agent upon task completion."""

    output: dict[str, Any] | str
    reasoning_summary: str  # Required by Constitution §Agent Role Taxonomy
    confidence: float | None = Field(None, ge=0.0, le=1.0)
    tool_calls: list[str] = Field(default_factory=list)


class A2ATask(BaseModel):
    """An asynchronous work unit managed by a Domain Agent's task store."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill: str
    params: dict[str, Any]
    status: TaskStatus = TaskStatus.SUBMITTED
    result: A2AResult | None = None
    error: str | None = None
    input_request: str | None = None  # Prompt when status = input-required
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def with_status(self, status: TaskStatus, **kwargs: Any) -> "A2ATask":
        """Return a copy with updated status and timestamp."""
        return self.model_copy(
            update={"status": status, "updated_at": datetime.now(timezone.utc), **kwargs}
        )


class AgentSkill(BaseModel):
    id: str
    description: str
    input_modes: list[str] = Field(default_factory=lambda: ["text"])
    output_modes: list[str] = Field(default_factory=lambda: ["text"])
    deprecated: bool = False
    sunset_date: str | None = None


class AgentCard(BaseModel):
    """Agent Card published at /.well-known/agent.json per A2A spec."""

    name: str
    version: str = "1.0.0"
    role: str  # "orchestrator" or "domain"
    domain: str | None = None  # Required when role = "domain"
    url: str
    description: str = ""
    skills: list[AgentSkill]
    security_schemes: dict[str, Any] = Field(
        default_factory=lambda: {"bearer": {"type": "http", "scheme": "bearer"}}
    )
    slo: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class PlanStep(BaseModel):
    """One step within an ExecutionPlan."""

    step_id: str
    agent: str  # "order-agent" | "bi-agent"
    skill: str
    params: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed
    result: dict[str, Any] | None = None
    task_id: str | None = None  # A2A task_id once dispatched


class ExecutionPlan(BaseModel):
    """Serializable Orchestrator plan, persisted to Redis before any A2A dispatch."""

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    goal: str
    intent: str
    steps: list[PlanStep] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    replan_count: int = 0
    max_replans: int = 3
    status: str = "active"  # active | completed | failed | replanning

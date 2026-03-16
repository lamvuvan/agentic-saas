"""Plan execution node — GPT-4o generates ExecutionPlan, persisted to Redis."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from shared.a2a.models import ExecutionPlan, PlanStep
from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "plan_v1.md"
_PROMPT_CACHE: str | None = None

_PLAN_SCHEMA = {
    "name": "execution_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "intent": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "string"},
                        "agent": {"type": "string"},
                        "skill": {"type": "string"},
                        "params": {"type": "string"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "status": {"type": "string"},
                    },
                    "required": ["step_id", "agent", "skill", "params", "depends_on", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["goal", "intent", "steps"],
        "additionalProperties": False,
    },
}

_PLAN_REDIS_TTL = 3600  # 1 hour


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


async def generate_plan(
    message: str,
    intent: str,
    session_id: str,
    redis: aioredis.Redis,
    trace_id: str = "",
    registry: Any = None,
) -> ExecutionPlan:
    """
    Generate an ExecutionPlan for the given message and intent.
    Persists the plan to Redis immediately (Constitution Principle V).
    """
    prompt = _load_prompt()
    # Inject live agent manifest from AgentRegistry when available
    if registry is not None:
        agent_manifest = registry.build_prompt_context()
    else:
        agent_manifest = (
            "## Available Domain Agents\n\n"
            "- **order-agent** (skill: `create_order`): Handles all order creation\n"
            "- **bi-agent** (skill: `bi_query`): Handles all business intelligence queries\n"
        )
    system_text = prompt.strip().replace("{agent_manifest}", agent_manifest)

    messages = [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"Intent: {intent} | Message: {message}"},
    ]

    content, _ = await chat_completion_async(
        messages=messages,
        task_type="orchestrator_plan",
        json_schema=_PLAN_SCHEMA,
        extra_log={"trace_id": trace_id, "session_id": session_id},
    )

    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("plan_parse_error", extra={"raw": content[:200]})
        # Fallback: create a stub plan
        raw = {
            "goal": f"Process {intent} request",
            "intent": intent,
            "steps": [{"step_id": "step-1", "agent": _intent_to_agent(intent),
                        "skill": _intent_to_skill(intent),
                        "params": {"message": message, "session_id": session_id},
                        "depends_on": [], "status": "pending"}],
        }

    # params may be a JSON-encoded string (Structured Outputs workaround) — parse it
    for step in raw.get("steps", []):
        p = step.get("params", {})
        if isinstance(p, str):
            try:
                step["params"] = json.loads(p)
            except (json.JSONDecodeError, ValueError):
                step["params"] = {}
        step["params"]["session_id"] = session_id

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        goal=raw["goal"],
        intent=raw["intent"],
        steps=[PlanStep(**s) for s in raw["steps"]],
    )

    # Persist immediately (Constitution Principle V — resilience)
    await redis.set(f"plan:{plan.plan_id}", plan.model_dump_json(), ex=_PLAN_REDIS_TTL)
    logger.info(
        "plan_persisted",
        extra={"plan_id": plan.plan_id, "session_id": session_id, "trace_id": trace_id},
    )

    return plan


def _intent_to_agent(intent: str) -> str:
    return "bi-agent" if intent == "bi_query" else "order-agent"


def _intent_to_skill(intent: str) -> str:
    return "bi_query" if intent == "bi_query" else "create_order"

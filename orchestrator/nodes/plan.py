"""Plan execution node — GPT-4o generates dual-layer ExecutionPlan, persisted to Redis + PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

from shared.a2a.models import ExecutionPlan, PlanStep
from shared.llm_client import chat_completion_async

if TYPE_CHECKING:
    from orchestrator.core.orchestrator_memory import OrchestratorMemoryService
    from orchestrator.core.plan_service import PlanService

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "plan_v1.md"
_PROMPT_CACHE: str | None = None

# Dual-layer schema: display (Vietnamese business layer) + routing (internal technical layer)
_PLAN_SCHEMA = {
    "name": "execution_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "display": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "sub_goals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sequence": {"type": "integer"},
                                "title": {"type": "string"},
                                "agent_name": {"type": "string"},
                                "agent_label": {"type": "string"},
                            },
                            "required": ["sequence", "title", "agent_name", "agent_label"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["goal", "sub_goals"],
                "additionalProperties": False,
            },
            "routing": {
                "type": "object",
                "properties": {
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
                "required": ["intent", "steps"],
                "additionalProperties": False,
            },
        },
        "required": ["display", "routing"],
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
    plan_service: "PlanService | None" = None,
    tenant_id: str = "default",
    memory: "OrchestratorMemoryService | None" = None,
) -> tuple[ExecutionPlan, str | None]:
    """
    Generate a dual-layer ExecutionPlan for the given message and intent.

    Layer 1 — display: Vietnamese business-friendly goal + sub_goals.
               Persisted to PostgreSQL via plan_service.create_plan() if available.
    Layer 2 — routing: technical steps for A2A dispatch.
               Persisted to Redis as plan:{plan_id} (Constitution Principle V).

    Memory injection (when memory is not None):
        - retrieve_patterns() → {routing_memory} block in system prompt
        - find_similar_plans() → {similar_plans} block in system prompt
        After plan persisted: fire-and-forget extract_and_store().

    Returns:
        (ExecutionPlan, pg_plan_id | None)
        - ExecutionPlan: routing plan for A2A dispatch
        - pg_plan_id: PostgreSQL plan ID if plan_service is provided, else None
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

    # Retrieve Orchestrator-level routing memory (best-effort; empty when memory=None)
    routing_memory_block = ""
    similar_plans_block = ""
    if memory is not None:
        try:
            patterns = await memory.retrieve_patterns(
                tenant_id=tenant_id,
                intent_class=intent,
                request_summary=message,
            )
            if patterns:
                lines = "\n".join(
                    f"- [{p['memory_type']}] {p['key']}: {p['content']}"
                    for p in patterns
                )
                routing_memory_block = f"## Routing Patterns\n\n{lines}"
        except Exception as exc:
            logger.debug("routing_memory_retrieve_failed", extra={"error": str(exc)})

        try:
            similar = await memory.find_similar_plans(
                tenant_id=tenant_id,
                user_message=message,
            )
            if similar:
                lines = "\n".join(
                    f"- Goal: {p['goal']} | user_message: {p['user_message']}"
                    for p in similar
                )
                similar_plans_block = f"## Plan Tương Tự\n\n{lines}"
        except Exception as exc:
            logger.debug("similar_plans_retrieve_failed", extra={"error": str(exc)})

    system_text = (
        prompt.strip()
        .replace("{agent_manifest}", agent_manifest)
        .replace("{routing_memory}", routing_memory_block)
        .replace("{similar_plans}", similar_plans_block)
    )

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
        display = raw["display"]
        routing = raw["routing"]
    except (json.JSONDecodeError, KeyError):
        logger.warning("plan_parse_error", extra={"raw": content[:200]})
        # Fallback: synthesize both layers from known intent
        agent = _intent_to_agent(intent)
        skill = _intent_to_skill(intent)
        label = _agent_label(agent)
        display = {
            "goal": f"Xử lý yêu cầu {intent}",
            "sub_goals": [{"sequence": 1, "title": message[:100], "agent_name": agent, "agent_label": label}],
        }
        routing = {
            "intent": intent,
            "steps": [{"step_id": "step-1", "agent": agent, "skill": skill,
                        "params": "injected_by_orchestrator", "depends_on": [], "status": "pending"}],
        }

    # Always override params with the actual message + session_id —
    # GPT params output is unreliable (may contain "PLACEHOLDER" or wrong values).
    for step in routing.get("steps", []):
        step["params"] = {"message": message, "session_id": session_id}

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id=session_id,
        goal=display["goal"],
        intent=routing.get("intent", intent),
        steps=[PlanStep(**s) for s in routing.get("steps", [])],
    )

    # Persist routing plan to Redis immediately (Constitution Principle V — resilience)
    await redis.set(f"plan:{plan.plan_id}", plan.model_dump_json(), ex=_PLAN_REDIS_TTL)
    logger.info(
        "plan_persisted",
        extra={"plan_id": plan.plan_id, "session_id": session_id, "trace_id": trace_id},
    )

    # Persist display plan to PostgreSQL (Plan Visibility — US7)
    pg_plan_id: str | None = None
    if plan_service is not None:
        try:
            pg_plan_id = await plan_service.create_plan(
                session_id=session_id,
                tenant_id=tenant_id,
                user_message=message,
                display=display,
            )
        except Exception as exc:
            logger.warning(
                "plan_service_create_failed",
                extra={"error": str(exc), "session_id": session_id},
            )

    # Fire-and-forget routing insight extraction (best-effort, never blocks response)
    if memory is not None:
        asyncio.create_task(
            _fire_and_forget_extract(memory, tenant_id, display, plan)
        )

    return plan, pg_plan_id


async def _fire_and_forget_extract(
    memory: "OrchestratorMemoryService",
    tenant_id: str,
    display: dict[str, Any],
    plan: ExecutionPlan,
) -> None:
    """Extract routing insights from a completed plan — best-effort, never re-raises."""
    try:
        sub_goals = display.get("sub_goals", [])
        plan_dict = {"goal": display.get("goal", ""), "id": plan.plan_id}
        await memory.extract_and_store(
            tenant_id=tenant_id,
            plan=plan_dict,
            sub_goals=sub_goals,
        )
    except Exception as exc:
        logger.warning("fire_and_forget_extract_failed", extra={"error": str(exc)})


def _intent_to_agent(intent: str) -> str:
    return "bi-agent" if intent == "bi_query" else "order-agent"


def _intent_to_skill(intent: str) -> str:
    return "bi_query" if intent == "bi_query" else "create_order"


def _agent_label(agent_name: str) -> str:
    labels = {
        "order-agent": "Tạo & Quản Lý Đơn Hàng",
        "bi-agent": "Báo Cáo & Phân Tích",
    }
    return labels.get(agent_name, agent_name)

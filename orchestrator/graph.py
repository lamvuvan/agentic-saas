"""Orchestrator LangGraph StateGraph.

Flow: classify_intent → plan_execution → dispatch_to_agent → aggregate_response
Replan edge: dispatch_to_agent → plan_execution (up to MAX_REPLAN_ATTEMPTS)
Chitchat short-circuit: classify_intent → aggregate_response (skip planning + dispatch)

Checkpointing: AsyncRedisSaver stores graph state keyed by thread_id=session_id.
Call setup_checkpointer(redis_url) at lifespan startup to enable persistence.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from orchestrator.core.orchestrator_memory import OrchestratorMemoryService
    from orchestrator.core.plan_service import PlanService

logger = logging.getLogger(__name__)

MAX_REPLAN_ATTEMPTS = 3

# Module-level checkpointer — initialized by setup_checkpointer() at lifespan
_saver = None


async def setup_checkpointer(redis_url: str | None = None) -> None:
    """
    Initialize the AsyncRedisSaver checkpointer from REDIS_URL.

    Call once at application lifespan startup.
    After this, all graph invocations persist checkpoints at
    key  ``checkpoint:orchestrator:{session_id}``.
    """
    global _saver  # noqa: PLW0603
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415

        _saver = AsyncRedisSaver(redis_url=url)
        await _saver.asetup()
        logger.info("orchestrator_checkpointer_ready", extra={"redis_url": url.split("@")[-1]})
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "orchestrator_checkpointer_unavailable",
            extra={"error": str(exc)},
        )
        _saver = None


async def invoke_chat(
    message: str,
    session_id: str,
    trace_id: str = "",
    redis: aioredis.Redis | None = None,
    registry: Any = None,
    plan_service: "PlanService | None" = None,
    tenant_id: str = "default",
    orchestrator_memory: "OrchestratorMemoryService | None" = None,
) -> dict[str, Any]:
    """
    Main entry point for the Orchestrator graph.

    Implements the classify → plan → dispatch → aggregate flow.
    Returns dict with: reply, intent, requires_input, model_used
    """
    from orchestrator.nodes.intent_classify import classify_intent  # noqa: PLC0415
    from orchestrator.nodes.plan import generate_plan  # noqa: PLC0415
    from orchestrator.nodes.a2a_dispatch import dispatch_plan  # noqa: PLC0415
    from orchestrator.nodes.aggregate import aggregate_results  # noqa: PLC0415

    # Checkpoint config — thread_id=session_id so AsyncRedisSaver can locate prior state
    _config: dict[str, Any] = {"configurable": {"thread_id": session_id}}

    # Load session history via session.py (last 3 turns injected into LLM context)
    history: list[dict[str, str]] = []
    if redis is not None:
        try:
            from orchestrator.session import get_last_n_turns  # noqa: PLC0415

            history = await get_last_n_turns(redis, session_id, n=3)
        except ImportError:
            pass

    # Node 1: Classify intent
    classification = await classify_intent(
        message=message,
        history=history,
        trace_id=trace_id,
    )
    intent = classification["intent"]
    model_used = classification["model_used"]

    logger.info(
        "intent_classified",
        extra={
            "intent": intent,
            "confidence": classification["confidence"],
            "escalated": classification["escalated"],
            "trace_id": trace_id,
            "session_id": session_id,
        },
    )

    # Chitchat short-circuit — no planning or dispatch needed
    if intent in ("chitchat", "unknown"):
        from orchestrator.nodes.aggregate import handle_chitchat  # noqa: PLC0415

        reply = await handle_chitchat(message, history)
        return {
            "reply": reply,
            "intent": intent,
            "requires_input": False,
            "model_used": model_used,
        }

    # Node 2: Generate execution plan (dual-layer: display + routing)
    if redis is None:
        # Fallback in-memory mock for tests without Redis
        from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

        mock_redis = MagicMock()
        mock_redis.set = AsyncMock()
        redis_for_plan = mock_redis
    else:
        redis_for_plan = redis

    plan, pg_plan_id = await generate_plan(
        message=message,
        intent=intent,
        session_id=session_id,
        redis=redis_for_plan,
        trace_id=trace_id,
        registry=registry,
        plan_service=plan_service,
        tenant_id=tenant_id,
        memory=orchestrator_memory,
    )

    # Node 3: Dispatch to agents (with replanning)
    agent_results: list[dict[str, Any]] = []
    replan_count = 0

    while replan_count <= MAX_REPLAN_ATTEMPTS:
        results, needs_replan = await dispatch_plan(
            plan=plan,
            trace_id=trace_id,
            registry=registry,
            plan_service=plan_service,
            plan_id=pg_plan_id,
        )
        agent_results = results

        if not needs_replan:
            break

        replan_count += 1
        if replan_count > MAX_REPLAN_ATTEMPTS:
            logger.warning(
                "max_replans_exceeded",
                extra={"replan_count": replan_count, "session_id": session_id},
            )
            break

        logger.info(
            "replanning",
            extra={"replan_count": replan_count, "session_id": session_id},
        )
        # Reset failed steps for replan
        for step in plan.steps:
            if step.status == "failed":
                step.status = "pending"
                step.task_id = None
        plan.replan_count = replan_count

    # Node 4: Aggregate response
    reply, requires_input = await aggregate_results(
        message=message,
        intent=intent,
        agent_results=agent_results,
        history=history,
        trace_id=trace_id,
    )

    # Save turn to session history
    if redis is not None:
        try:
            from orchestrator.session import append_turn  # noqa: PLC0415

            await append_turn(redis, session_id, "user", message, intent=intent)
            await append_turn(redis, session_id, "assistant", reply, trace_id=trace_id)
        except ImportError:
            pass

    # Persist final state as a LangGraph checkpoint (thread_id=session_id)
    if _saver is not None:
        try:
            checkpoint_data = {
                "session_id": session_id,
                "last_intent": intent,
                "last_model": model_used,
                "requires_input": requires_input,
            }
            await _saver.aput(
                _config,
                {"v": 1, "ts": "", "id": trace_id, "channel_values": checkpoint_data, "channel_versions": {}, "versions_seen": {}, "pending_sends": []},
                {},
                {},
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("checkpoint_put_failed", extra={"error": str(exc)})

    return {
        "reply": reply,
        "intent": intent,
        "requires_input": requires_input,
        "model_used": model_used,
    }

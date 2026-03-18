"""Orchestrator LangGraph StateGraph.

Flow: classify_intent → plan_execution → dispatch_to_agent → respond
Chitchat short-circuit: classify_intent → chitchat → respond (skip planning + dispatch)

State: OrchestratorState (TypedDict) — messages field uses add_messages reducer
Checkpointing: AsyncRedisSaver keyed by thread_id=session_id (24h TTL).

Resume a session:
    await invoke_chat(message, session_id=existing_session_id, ...)
    The graph loads the prior checkpoint automatically via thread_id.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from orchestrator.core.orchestrator_memory import OrchestratorMemoryService
    from orchestrator.core.plan_service import PlanService

logger = logging.getLogger(__name__)

MAX_REPLAN_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# State schema (T024)
# ---------------------------------------------------------------------------


class OrchestratorState(TypedDict, total=False):
    """Orchestrator LangGraph state.

    ``messages`` uses the ``add_messages`` reducer — each ainvoke call APPENDS
    new messages rather than replacing the list.  This gives us append-only
    conversation history across graph invocations with the same thread_id.
    """

    session_id: str
    tenant_id: str
    messages: Annotated[list[BaseMessage], add_messages]
    intent: str
    plan_id: str
    plan: dict
    dispatch_results: dict
    hitl_pending: dict | None
    final_response: str


# ---------------------------------------------------------------------------
# Module-level compiled graph — initialized by setup_graph() at lifespan
# ---------------------------------------------------------------------------

_compiled = None


async def setup_graph(redis_url: str | None = None) -> None:
    """Initialize the compiled StateGraph with AsyncRedisSaver checkpointer.

    Call once at FastAPI lifespan startup.
    """
    global _compiled  # noqa: PLW0603
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415

        saver = AsyncRedisSaver(redis_url=url)
        await saver.asetup()
        _compiled = _build_graph().compile(checkpointer=saver)
        logger.info("orchestrator_graph_ready", extra={"redis_url": url.split("@")[-1]})
    except Exception as exc:  # pragma: no cover
        logger.warning("orchestrator_graph_unavailable", extra={"error": str(exc)})
        # Fallback: compile without checkpointer (no persistence)
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        _compiled = _build_graph().compile(checkpointer=MemorySaver())


# Keep backward-compat alias used by existing lifespan code
async def setup_checkpointer(redis_url: str | None = None) -> None:
    await setup_graph(redis_url)


# ---------------------------------------------------------------------------
# Node wrapper functions (T031)
# ---------------------------------------------------------------------------


async def _classify_intent_node(state: OrchestratorState, config: dict) -> dict:
    from orchestrator.nodes.intent_classify import classify_intent  # noqa: PLC0415

    messages = state.get("messages", [])
    user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    message = user_msgs[-1].content if user_msgs else ""
    history: list[dict[str, str]] = config.get("configurable", {}).get("history", [])
    trace_id = state.get("session_id", "")

    classification = await classify_intent(message=message, history=history, trace_id=trace_id)
    logger.info(
        "intent_classified",
        extra={
            "intent": classification["intent"],
            "confidence": classification["confidence"],
            "session_id": state.get("session_id", ""),
        },
    )
    return {"intent": classification["intent"]}


async def _chitchat_node(state: OrchestratorState, config: dict) -> dict:
    from orchestrator.nodes.aggregate import handle_chitchat  # noqa: PLC0415

    messages = state.get("messages", [])
    user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    message = user_msgs[-1].content if user_msgs else ""
    history: list[dict[str, str]] = config.get("configurable", {}).get("history", [])

    reply = await handle_chitchat(message, history)
    return {"final_response": reply}


async def _plan_node(state: OrchestratorState, config: dict) -> dict:
    from orchestrator.nodes.plan import generate_plan  # noqa: PLC0415

    cfg = config.get("configurable", {})
    messages = state.get("messages", [])
    user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    message = user_msgs[-1].content if user_msgs else ""

    plan, pg_plan_id = await generate_plan(
        message=message,
        intent=state.get("intent", ""),
        session_id=state.get("session_id", ""),
        redis=cfg.get("redis"),
        trace_id=state.get("session_id", ""),
        registry=cfg.get("registry"),
        plan_service=cfg.get("plan_service"),
        tenant_id=state.get("tenant_id", "default"),
        memory=cfg.get("orchestrator_memory"),
    )
    return {"plan": plan, "plan_id": pg_plan_id or ""}


async def _dispatch_node(state: OrchestratorState, config: dict) -> dict:
    from orchestrator.nodes.a2a_dispatch import dispatch_plan  # noqa: PLC0415

    cfg = config.get("configurable", {})
    messages = state.get("messages", [])
    user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    message = user_msgs[-1].content if user_msgs else ""
    history: list[dict[str, str]] = cfg.get("history", [])

    plan = state.get("plan")
    if plan is None:
        return {"dispatch_results": {}}

    replan_count = 0
    agent_results: list[dict[str, Any]] = []

    while replan_count <= MAX_REPLAN_ATTEMPTS:
        results, needs_replan = await dispatch_plan(
            plan=plan,
            trace_id=state.get("session_id", ""),
            registry=cfg.get("registry"),
            plan_service=cfg.get("plan_service"),
            plan_id=state.get("plan_id", ""),
            session={
                "session_id": state.get("session_id", ""),
                "turns": history,
                "last_user_message": message,
            },
            tenant_id=state.get("tenant_id", "default"),
        )
        agent_results = results

        if not needs_replan:
            break

        replan_count += 1
        if replan_count > MAX_REPLAN_ATTEMPTS:
            logger.warning(
                "max_replans_exceeded",
                extra={"replan_count": replan_count, "session_id": state.get("session_id", "")},
            )
            break

        for step in plan.steps:
            if step.status == "failed":
                step.status = "pending"
                step.task_id = None
        plan.replan_count = replan_count

    return {"dispatch_results": {"agent_results": agent_results}}


async def _respond_node(state: OrchestratorState, config: dict) -> dict:
    from orchestrator.nodes.aggregate import aggregate_results  # noqa: PLC0415

    cfg = config.get("configurable", {})
    messages = state.get("messages", [])
    user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    message = user_msgs[-1].content if user_msgs else ""
    history: list[dict[str, str]] = cfg.get("history", [])

    # Chitchat path: final_response already set
    if state.get("final_response"):
        return {}

    dispatch_results = state.get("dispatch_results", {})
    agent_results = dispatch_results.get("agent_results", [])

    reply, requires_input = await aggregate_results(
        message=message,
        intent=state.get("intent", ""),
        agent_results=agent_results,
        history=history,
        trace_id=state.get("session_id", ""),
    )
    return {"final_response": reply}


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def _route_after_classify(state: OrchestratorState) -> str:
    intent = state.get("intent", "")
    if intent in ("chitchat", "unknown"):
        return "chitchat"
    return "plan"


# ---------------------------------------------------------------------------
# StateGraph assembly (T031)
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    g = StateGraph(OrchestratorState)

    g.add_node("classify_intent", _classify_intent_node)
    g.add_node("chitchat", _chitchat_node)
    g.add_node("plan", _plan_node)
    g.add_node("dispatch", _dispatch_node)
    g.add_node("respond", _respond_node)

    g.set_entry_point("classify_intent")
    g.add_conditional_edges(
        "classify_intent",
        _route_after_classify,
        {"chitchat": "chitchat", "plan": "plan"},
    )
    g.add_edge("plan", "dispatch")
    g.add_edge("dispatch", "respond")
    g.add_edge("chitchat", "respond")
    g.add_edge("respond", END)

    return g


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def invoke_chat(
    message: str,
    session_id: str,
    trace_id: str = "",
    redis: "aioredis.Redis | None" = None,
    registry: Any = None,
    plan_service: "PlanService | None" = None,
    tenant_id: str = "default",
    orchestrator_memory: "OrchestratorMemoryService | None" = None,
) -> dict[str, Any]:
    """Main entry point for the Orchestrator graph.

    Submits a HumanMessage and runs the StateGraph with thread_id=session_id.
    The AsyncRedisSaver automatically loads prior checkpoints for the same
    session_id, giving us persistent multi-turn conversation state.

    Returns dict with: reply, intent, requires_input, model_used
    """
    # Load session history for context injection into node prompts
    history: list[dict[str, str]] = []
    if redis is not None:
        try:
            from orchestrator.session import get_last_n_turns  # noqa: PLC0415

            history = await get_last_n_turns(redis, session_id, n=3)
        except Exception:
            pass

    initial_state: OrchestratorState = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "messages": [HumanMessage(content=message)],
        "intent": "",
        "plan_id": "",
        "plan": {},
        "dispatch_results": {},
        "hitl_pending": None,
        "final_response": "",
    }

    config = {
        "configurable": {
            "thread_id": session_id,
            "redis": redis,
            "registry": registry,
            "plan_service": plan_service,
            "orchestrator_memory": orchestrator_memory,
            "history": history,
        }
    }

    graph = _compiled
    if graph is None:
        # Fallback: build in-memory graph if setup_graph() was not called
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        graph = _build_graph().compile(checkpointer=MemorySaver())

    result_state = await graph.ainvoke(initial_state, config)

    reply = result_state.get("final_response", "")
    intent = result_state.get("intent", "unknown")

    # Persist to session history
    if redis is not None:
        try:
            from orchestrator.session import append_turn  # noqa: PLC0415

            await append_turn(redis, session_id, "user", message, intent=intent)
            await append_turn(redis, session_id, "assistant", reply, trace_id=trace_id)
        except Exception:
            pass

    return {
        "reply": reply,
        "intent": intent,
        "requires_input": False,
        "model_used": "gpt-4o-mini",
    }

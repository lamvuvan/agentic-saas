"""Order Agent LangGraph StateGraph (T038, T046).

Flow:
    extract_entities → match_products → check_customer → preview_order
        ↓ (ready)              ↑ (modify: loop back)
    [interrupt_before] hitl_confirm → create_order → done → END
        ↓ (unresolved products / customer not found)
    ← back to extract_entities

Checkpointing: AsyncRedisSaver keyed by thread_id=task_id (TTL=1h).

HITL resume pattern:
    # Initial run hits interrupt, returns state with confirm_message set:
    result = await compiled.ainvoke(initial_state, config)
    # → result["confirm_message"] is set; A2A server sets status=input-required

    # Resume after user responds:
    from langgraph.types import Command
    result = await compiled.ainvoke(
        Command(resume={"user_confirmation": user_response}),
        config={"configurable": {"thread_id": task_id}}
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langgraph.graph import END, StateGraph

from order_agent.models import (
    OrderAgentState,
    OrderDraft,
    OrderEntities,
    ProductMatch,
    ResolvedOrderItem,
)

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_URL = os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001")

# ---------------------------------------------------------------------------
# Module-level compiled graph — initialized by setup_checkpointer() at lifespan
# ---------------------------------------------------------------------------

_compiled = None


async def setup_checkpointer(redis_url: str | None = None) -> None:
    """Initialize compiled StateGraph with AsyncRedisSaver.

    Call once at FastAPI lifespan startup.  After this, HITL interrupts are
    automatically checkpointed by LangGraph at the 'hitl_confirm' node boundary.
    """
    global _compiled  # noqa: PLW0603
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415

        saver = AsyncRedisSaver(redis_url=url)
        await saver.asetup()
        _compiled = _build_graph().compile(
            checkpointer=saver,
            interrupt_before=["hitl_confirm"],
        )
        logger.info("order_graph_ready", extra={"redis_url": url.split("@")[-1]})
    except Exception as exc:  # pragma: no cover
        logger.warning("order_graph_unavailable", extra={"error": str(exc)})
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        _compiled = _build_graph().compile(
            checkpointer=MemorySaver(),
            interrupt_before=["hitl_confirm"],
        )


# ---------------------------------------------------------------------------
# Node wrapper functions
# ---------------------------------------------------------------------------


async def _extract_entities_node(state: OrderAgentState, config: dict) -> dict:
    """Extract order entities from Vietnamese natural language input."""
    from order_agent.nodes.extract_entities import extract_entities  # noqa: PLC0415

    message = state.get("original_message", "")
    task_id = state.get("task_id", "")

    # Reset downstream state when re-running (modify loop)
    entities = await extract_entities(message=message, trace_id=task_id)

    if entities.intent_modifier == "cancel":
        return {
            "result": {
                "status": "cancelled",
                "message": "Đã huỷ yêu cầu.",
            },
            "entities": entities.model_dump(),
        }

    return {
        "entities": entities.model_dump(),
        "matched_products": [],
        "customer": {},
        "order_preview": {},
        "confirm_message": "",
    }


async def _match_products_node(state: OrderAgentState, config: dict) -> dict:
    """Match extracted order items against FAISS product index."""
    # Short-circuit: already cancelled
    result = state.get("result", {})
    if result.get("status") == "cancelled":
        return {}

    product_matcher = config.get("configurable", {}).get("product_matcher")
    if product_matcher is None:
        # Stub mode (no matcher available)
        return {"matched_products": []}

    from order_agent.nodes.match_products import match_products  # noqa: PLC0415

    entities_dict = state.get("entities", {})
    try:
        entities = OrderEntities.model_validate(entities_dict)
    except Exception:
        return {"matched_products": []}

    matches, unresolved = await match_products(
        entities=entities,
        product_matcher=product_matcher,
        trace_id=state.get("task_id", ""),
    )

    if unresolved:
        ask_msg = (
            "Có một số sản phẩm chưa xác định được:\n"
            + "\n".join(f"- '{q}': Vui lòng mô tả lại." for q in unresolved)
        )
        return {
            "matched_products": [m.model_dump() for m in matches],
            "confirm_message": ask_msg,
        }

    return {"matched_products": [m.model_dump() for m in matches]}


async def _check_customer_node(state: OrderAgentState, config: dict) -> dict:
    """Look up customer by name via Tool Registry."""
    # Short-circuit: already cancelled or need product clarification
    result = state.get("result", {})
    if result.get("status") == "cancelled":
        return {}
    if state.get("confirm_message"):
        return {}

    from order_agent.nodes.check_customer import check_customer  # noqa: PLC0415

    tool_registry_url = config.get("configurable", {}).get(
        "tool_registry_url", _TOOL_REGISTRY_URL
    )
    entities_dict = state.get("entities", {})
    customer_name = entities_dict.get("customer_name")

    customer_id, resolved_name, input_request = await check_customer(
        customer_name=customer_name,
        tool_registry_url=tool_registry_url,
    )

    if input_request:
        return {
            "customer": {"not_found": True, "customer_name": customer_name},
            "confirm_message": input_request,
        }

    return {
        "customer": {
            "customer_id": customer_id,
            "customer_name": resolved_name or customer_name,
            "not_found": customer_id is None,
        }
    }


async def _preview_order_node(state: OrderAgentState, config: dict) -> dict:
    """Build order preview and confirmation message."""
    # Short-circuit conditions
    result = state.get("result", {})
    if result.get("status") == "cancelled":
        return {}
    if state.get("confirm_message"):
        # Already have a pending question (unresolved product or customer)
        return {}

    from order_agent.nodes.preview import build_preview  # noqa: PLC0415

    entities_dict = state.get("entities", {})
    try:
        entities = OrderEntities.model_validate(entities_dict)
    except Exception:
        return {}

    matched_products_raw = state.get("matched_products", [])
    matches: list[ProductMatch] = []
    for m in matched_products_raw:
        try:
            matches.append(ProductMatch.model_validate(m))
        except Exception:
            pass

    customer = state.get("customer", {})
    customer_id = customer.get("customer_id")
    customer_name = customer.get("customer_name")

    draft, preview_message = build_preview(
        entities=entities,
        matches=matches,
        customer_id=customer_id,
        customer_name=customer_name or entities.customer_name,
        session_id=state.get("task_id", ""),
    )

    # Build preview dict from draft
    preview = {
        "items": [item.model_dump() for item in draft.items],
        "table_number": draft.table_number,
        "customer_id": draft.customer_id,
        "customer_name": draft.customer_name,
        "discount": draft.discount,
        "notes": draft.notes,
        "total_estimate": draft.calculate_total(),
        "ready": len(draft.items) > 0 and customer_id is not None,
    }

    return {
        "order_preview": preview,
        "confirm_message": preview_message,
    }


async def _hitl_confirm_node(state: OrderAgentState, config: dict) -> dict:
    """HITL confirm node — runs after interrupt fires.

    ``state["user_confirmation"]`` is set by Command(resume={"user_confirmation": ...}).
    """
    from order_agent.nodes.hitl_confirm import hitl_confirm  # noqa: PLC0415

    return await hitl_confirm(state, config)


async def _create_order_node(state: OrderAgentState, config: dict) -> dict:
    """Submit confirmed order to backend."""
    from order_agent.nodes.create_order import create_order  # noqa: PLC0415

    return await create_order(state, config)


async def _done_node(state: OrderAgentState, config: dict) -> dict:
    """Record task history and finalize."""
    from order_agent.nodes.done import done  # noqa: PLC0415

    return await done(state, config)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _should_confirm(state: OrderAgentState) -> str:
    """Route from preview_order: if ready → hitl_confirm, else ask user (re-route)."""
    # Cancelled or terminal result
    result = state.get("result", {})
    if result.get("status") == "cancelled":
        return "done"

    order_preview = state.get("order_preview", {})
    if order_preview.get("ready"):
        return "hitl_confirm"

    # Not ready: confirm_message is set with a question for the user
    # We route to done to surface the question via result
    if state.get("confirm_message"):
        return "done"

    return "done"


def _route_after_confirm(state: OrderAgentState) -> str:
    """Route from hitl_confirm based on user intent."""
    result = state.get("result", {})
    intent = result.get("__hitl_intent__", "cancel")

    if intent == "confirm":
        return "create_order"
    if intent == "modify":
        return "extract_entities"
    # cancel or scope_change → done
    return "done"


# ---------------------------------------------------------------------------
# StateGraph assembly (T046)
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    g = StateGraph(OrderAgentState)

    g.add_node("extract_entities", _extract_entities_node)
    g.add_node("match_products", _match_products_node)
    g.add_node("check_customer", _check_customer_node)
    g.add_node("preview_order", _preview_order_node)
    g.add_node("hitl_confirm", _hitl_confirm_node)
    g.add_node("create_order", _create_order_node)
    g.add_node("done", _done_node)

    g.set_entry_point("extract_entities")
    g.add_edge("extract_entities", "match_products")
    g.add_edge("match_products", "check_customer")
    g.add_edge("check_customer", "preview_order")
    g.add_conditional_edges(
        "preview_order",
        _should_confirm,
        {
            "hitl_confirm": "hitl_confirm",
            "done": "done",
        },
    )
    g.add_conditional_edges(
        "hitl_confirm",
        _route_after_confirm,
        {
            "create_order": "create_order",
            "extract_entities": "extract_entities",
            "done": "done",
        },
    )
    g.add_edge("create_order", "done")
    g.add_edge("done", END)

    return g


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_order_graph(
    task_id: str,
    params: dict[str, Any],
    redis: Any = None,
    product_matcher: Any = None,
) -> dict[str, Any]:
    """Execute the Order Agent pipeline for a task.

    Handles:
    - New order: extract → match → customer → preview → (interrupt) confirm → submit
    - Continuation: pass Command(resume={"user_confirmation": ...}) as first arg

    Returns A2AResult-shaped dict.
    """
    graph = _compiled
    if graph is None:
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        graph = _build_graph().compile(
            checkpointer=MemorySaver(),
            interrupt_before=["hitl_confirm"],
        )

    tool_registry_url = os.environ.get("TOOL_REGISTRY_URL", _TOOL_REGISTRY_URL)

    initial_state: OrderAgentState = {
        "task_id": task_id,
        "plan_id": params.get("plan_id", ""),
        "original_message": params.get("message", ""),
        "instructions": params.get("instructions", ""),
        "conversation_history": params.get("conversation_history", []),
        "dependency_results": params.get("dependency_results", {}),
        "memory_context": "",
        "entities": {},
        "matched_products": [],
        "customer": {},
        "order_preview": {},
        "confirm_message": "",
        "user_confirmation": "",
        "result": {},
    }

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": task_id,
            "product_matcher": product_matcher,
            "tool_registry_url": tool_registry_url,
            "memory": None,
        }
    }

    result_state = await graph.ainvoke(initial_state, config)

    # Interrupted at hitl_confirm: confirm_message set, user_confirmation empty
    if result_state.get("confirm_message") and not result_state.get("user_confirmation"):
        return {
            "status": "input-required",
            "input_request": result_state["confirm_message"],
            "reasoning_summary": "Order preview built, awaiting user confirmation.",
            "tool_calls": [],
            "output": {
                "status": "preview",
                "order_preview": result_state.get("order_preview", {}),
                "message": result_state["confirm_message"],
            },
        }

    final_result = result_state.get("result", {})
    a2a_result = final_result.get("__a2a_result__", final_result)

    return {
        "output": a2a_result.get("output", final_result) if isinstance(a2a_result, dict) else final_result,
        "reasoning_summary": (
            a2a_result.get("reasoning_summary", "Order processed.")
            if isinstance(a2a_result, dict)
            else "Order processed."
        ),
        "tool_calls": a2a_result.get("tool_calls", []) if isinstance(a2a_result, dict) else [],
        "status": final_result.get("status", "completed"),
    }

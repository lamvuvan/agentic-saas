"""Order Agent LangGraph pipeline.

Flow: extract_entities → match_products → check_customer → build_preview
      → [interrupt: confirm] → submit_order

Checkpointing: AsyncRedisSaver stores graph state keyed by thread_id=task_id.
Call setup_checkpointer(redis_url) at lifespan startup.
Resume an interrupted confirmation via ainvoke(None, {configurable: {thread_id: task_id}}).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_URL = os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001")

# Module-level checkpointer — initialized by setup_checkpointer() at lifespan
_saver = None


async def setup_checkpointer(redis_url: str | None = None) -> None:
    """
    Initialize the AsyncRedisSaver checkpointer from REDIS_URL.

    After setup, interrupted order confirmation flows can be resumed via:
        await run_order_graph(task_id, {"continuation": {...}}, redis)
    The draft state is persisted at ``order:draft:{task_id}`` in Redis.
    """
    global _saver  # noqa: PLW0603
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415

        _saver = AsyncRedisSaver(redis_url=url)
        await _saver.asetup()
        logger.info("order_checkpointer_ready", extra={"redis_url": url.split("@")[-1]})
    except Exception as exc:  # pragma: no cover
        logger.warning("order_checkpointer_unavailable", extra={"error": str(exc)})
        _saver = None


async def run_order_graph(
    task_id: str,
    params: dict[str, Any],
    redis: aioredis.Redis,
    product_matcher=None,
) -> dict[str, Any]:
    """
    Execute the Order Agent pipeline for a task.

    Handles:
    - New order: extract → match → customer → preview → (interrupt) confirm → submit
    - Continuation: resume from saved state using user's confirmation input
    - Add/cancel modifiers

    Returns dict with:
    - status: "input-required" | "confirmed" | "cancelled" | "error"
    - output / input_request / reasoning_summary / tool_calls
    """
    from order_agent.nodes.extract_entities import extract_entities  # noqa: PLC0415
    from order_agent.nodes.match_products import match_products  # noqa: PLC0415
    from order_agent.nodes.check_customer import check_customer  # noqa: PLC0415
    from order_agent.nodes.preview import build_preview  # noqa: PLC0415
    from order_agent.nodes.confirm import check_confirmation  # noqa: PLC0415
    from order_agent.nodes.submit_order import submit_order  # noqa: PLC0415

    # Checkpoint config — thread_id=task_id so AsyncRedisSaver keys this graph's state
    _config: dict[str, Any] = {"configurable": {"thread_id": task_id}}

    message = params.get("message", "")
    session_id = params.get("session_id", task_id)
    trace_id = params.get("_trace_id", task_id)
    continuation = params.get("continuation")
    tool_calls: list[str] = []

    # ── Continuation path (resume after input-required) ──────────────────────
    # Resume via ainvoke(None, config) pattern: load checkpoint keyed by original task_id.
    # a2a_server may pre-load _draft_json from the AsyncRedisSaver checkpoint.
    if continuation:
        user_input = continuation.get("user_input", "")
        original_task_id = continuation.get("task_id", task_id)

        # Use pre-loaded draft if available (passed by a2a_server's resume path)
        draft_json = continuation.get("_draft_json") or await redis.get(
            f"order:draft:{original_task_id}"
        )
        if draft_json:
            from order_agent.models import OrderDraft  # noqa: PLC0415

            draft = OrderDraft.model_validate_json(draft_json)
            confirmation = check_confirmation(user_input)

            if confirmation == "confirmed":
                tool_calls.append("order__create_order")
                result = await submit_order(draft, _TOOL_REGISTRY_URL)
                return {
                    "output": result,
                    "reasoning_summary": f"User confirmed order. Submitted {len(draft.items)} items.",
                    "tool_calls": tool_calls,
                    "status": result.get("status", "confirmed"),
                }

            elif confirmation == "cancelled":
                return {
                    "output": {"status": "cancelled", "message": "Đơn hàng đã huỷ."},
                    "reasoning_summary": "User cancelled the order.",
                    "tool_calls": [],
                    "status": "cancelled",
                }

            else:
                return {
                    "status": "input-required",
                    "input_request": "Bạn có xác nhận đơn hàng không? Vui lòng trả lời 'xác nhận' hoặc 'huỷ'.",
                    "reasoning_summary": "Unclear confirmation response; asking again.",
                    "tool_calls": [],
                }

    # ── New order path ────────────────────────────────────────────────────────

    # Step 1: Extract entities
    entities = await extract_entities(message, trace_id=trace_id)

    if entities.intent_modifier == "cancel":
        return {
            "output": {"status": "cancelled", "message": "Đã huỷ yêu cầu."},
            "reasoning_summary": "User explicitly cancelled.",
            "tool_calls": [],
            "status": "cancelled",
        }

    # Step 2: Match products
    if product_matcher is None:
        # For tests without a real matcher: return preview with unresolved items
        return _fallback_preview(entities, session_id, task_id)

    matches, unresolved = await match_products(entities, product_matcher, trace_id=trace_id)

    # If any items are unresolved, ask user to clarify
    if unresolved:
        candidates_text = "\n".join(
            f"- '{q}': Không tìm thấy sản phẩm phù hợp. Vui lòng mô tả lại."
            for q in unresolved
        )
        return {
            "status": "input-required",
            "input_request": f"Có một số sản phẩm chưa xác định được:\n{candidates_text}",
            "reasoning_summary": f"Could not match {len(unresolved)} product(s).",
            "tool_calls": [],
        }

    # Step 3: Check customer
    tool_calls.append("customer__get_customers")
    customer_id, customer_name, customer_input_request = await check_customer(
        entities.customer_name,
        _TOOL_REGISTRY_URL,
    )

    if customer_input_request:
        return {
            "status": "input-required",
            "input_request": customer_input_request,
            "reasoning_summary": "Customer lookup requires user clarification.",
            "tool_calls": tool_calls,
        }

    # Step 4: Build preview
    draft, preview_message = build_preview(
        entities=entities,
        matches=matches,
        customer_id=customer_id,
        customer_name=customer_name or entities.customer_name,
        session_id=session_id,
    )

    # Persist draft for continuation (used by ainvoke(None, config) resume path)
    await redis.set(f"order:draft:{task_id}", draft.model_dump_json(), ex=3600)

    # Persist checkpoint so AsyncRedisSaver can resume this interrupted flow
    if _saver is not None:
        try:
            checkpoint_data = {
                "task_id": task_id,
                "session_id": session_id,
                "status": "input-required",
                "draft_key": f"order:draft:{task_id}",
            }
            await _saver.aput(
                _config,
                {
                    "v": 1,
                    "ts": "",
                    "id": task_id,
                    "channel_values": checkpoint_data,
                    "channel_versions": {},
                    "versions_seen": {},
                    "pending_sends": [],
                },
                {},
                {},
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("order_checkpoint_put_failed", extra={"error": str(exc)})

    return {
        "status": "input-required",
        "input_request": preview_message,
        "reasoning_summary": f"Extracted {len(entities.items)} items, built order preview.",
        "tool_calls": tool_calls,
        "output": {
            "status": "preview",
            "order_draft": draft.model_dump(),
            "message": preview_message,
        },
    }


def _fallback_preview(entities, session_id: str, task_id: str) -> dict[str, Any]:
    """Fallback when no product_matcher is available (test/stub mode)."""
    items_text = ", ".join(
        f"{item.product_query} × {item.quantity}" for item in entities.items
    )
    preview = (
        f"📋 Đơn hàng dự kiến:\n"
        f"  • {items_text}\n"
        f"✅ Xác nhận đơn hàng không? (xác nhận / huỷ)"
    )
    return {
        "status": "input-required",
        "input_request": preview,
        "reasoning_summary": f"Preview built for {len(entities.items)} items (stub mode).",
        "tool_calls": [],
        "output": {"status": "preview", "message": preview},
    }

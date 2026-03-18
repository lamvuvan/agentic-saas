"""Done node — record task history, extract learnings, finalize A2AResult (T045)."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def _extract_and_store_learnings(state: dict, memory) -> None:
    """Best-effort: extract learnable facts from a completed order task."""
    try:
        from shared.llm_client import chat_completion_async  # noqa: PLC0415
        import json  # noqa: PLC0415

        order_result = state.get("result", {})
        entities = state.get("entities", {})
        customer = state.get("customer", {})

        if not order_result.get("order_code"):
            return

        prompt = (
            f"Đơn hàng vừa tạo: {json.dumps(order_result, ensure_ascii=False)}\n"
            f"Thực thể trích xuất: {json.dumps(entities, ensure_ascii=False)}\n"
            f"Khách hàng: {json.dumps(customer, ensure_ascii=False)}\n"
            "Trích xuất tối đa 2 fact học được (alias tên khách, sở thích món, ...). "
            "Trả về JSON: {\"facts\": [{\"type\": str, \"key\": str, \"value\": str}]}"
        )
        raw = await chat_completion_async(
            messages=[{"role": "user", "content": prompt}],
            system="Bạn là hệ thống học từ dữ liệu lịch sử đơn hàng.",
            task_type="entity_extract",
        )
        data = json.loads(raw)
        for fact in data.get("facts", [])[:2]:
            await memory.store(
                tenant_id="default",
                memory_type=fact.get("type", "order_pattern"),
                key=fact.get("key", ""),
                content=fact.get("value", ""),
                confidence=0.7,
            )
    except Exception as exc:
        logger.debug("learning_extraction_failed", extra={"error": str(exc)})


async def done(state: dict, config: dict) -> dict:
    """Finalize the order task — record history and build A2AResult."""
    cfg = config.get("configurable", {})
    memory = cfg.get("memory")

    result = state.get("result", {})
    entities = state.get("entities", {})
    matched_products = state.get("matched_products", [])
    task_id = state.get("task_id", "")

    outcome = "success" if result.get("status") == "confirmed" else "failure"
    items_count = len(matched_products)
    order_code = result.get("order_code", "")

    if memory is not None:
        try:
            await memory.record_task(
                tenant_id=cfg.get("tenant_id", "default"),
                plan_id=state.get("plan_id", ""),
                skill="create_order",
                input_summary=state.get("original_message", "")[:100],
                outcome=outcome,
                key_decisions=[
                    f"items={items_count}",
                    f"order_code={order_code}",
                ],
                learnings=[],
                duration_ms=0,
            )
            # Best-effort learning extraction (fire-and-forget)
            asyncio.create_task(_extract_and_store_learnings(state, memory))
        except Exception as exc:
            logger.debug("task_record_failed", extra={"error": str(exc), "task_id": task_id})

    tool_calls = ["order__create_order"] if result.get("order_code") else []

    return {
        "result": {
            **result,
            "__a2a_result__": {
                "output": {k: v for k, v in result.items() if not k.startswith("__")},
                "reasoning_summary": (
                    f"Order created: {order_code}" if order_code
                    else f"Order {result.get('status', 'processed')}: {result.get('message', '')[:80]}"
                ),
                "tool_calls": tool_calls,
            },
        }
    }

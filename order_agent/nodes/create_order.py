"""Create order node — submits confirmed order via Tool Registry (T044)."""

from __future__ import annotations

import logging
import os

from order_agent.models import OrderDraft, ResolvedOrderItem
from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError

logger = logging.getLogger(__name__)


async def create_order(state: dict, config: dict) -> dict:
    """Submit confirmed order to backend via Tool Registry.

    Reads ``order_preview`` and ``customer`` from state to reconstruct the order.
    Skips submission if ``result.__hitl_intent__`` is not "confirm" (cancel/scope_change path).
    """
    result_so_far = state.get("result", {})
    hitl_intent = result_so_far.get("__hitl_intent__", "confirm")

    # If cancelled or scope_change — skip submission, pass through to done
    if hitl_intent in ("cancel", "scope_change"):
        return {}

    tool_registry_url = config.get("configurable", {}).get(
        "tool_registry_url",
        os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001"),
    )

    order_preview = state.get("order_preview", {})
    customer = state.get("customer", {})

    # Reconstruct draft from state
    items = []
    for item in order_preview.get("items", []):
        if isinstance(item, dict):
            items.append(
                ResolvedOrderItem(
                    product_id=item.get("product_id", ""),
                    product_name=item.get("product_name", ""),
                    quantity=item.get("quantity", 1),
                    price=item.get("price", 0.0),
                    note=item.get("note"),
                )
            )

    draft = OrderDraft(
        session_id=state.get("task_id", ""),
        customer_id=customer.get("customer_id"),
        customer_name=customer.get("customer_name"),
        table_number=order_preview.get("table_number"),
        items=items,
        discount=order_preview.get("discount", 0.0),
        notes=order_preview.get("notes"),
    )

    client = ToolRegistryClient(base_url=tool_registry_url)
    payload = {
        "customer_id": draft.customer_id,
        "table_number": draft.table_number,
        "items": [
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "price": item.price,
                "note": item.note,
            }
            for item in draft.items
        ],
        "discount": draft.discount,
        "notes": draft.notes,
    }

    try:
        api_result = await client.execute("order__create_order", payload)
        order_code = api_result.get("code") or api_result.get("order_code") or api_result.get("id", "")
        logger.info("order_created", extra={"order_code": order_code, "task_id": state.get("task_id", "")})
        return {
            "result": {
                "status": "confirmed",
                "order_code": order_code,
                "message": f"✅ Đơn hàng đã được tạo thành công! Mã đơn: {order_code}",
                "__hitl_intent__": "confirm",
            }
        }
    except ToolRegistryError as exc:
        logger.error("order_create_failed", extra={"error": str(exc), "task_id": state.get("task_id", "")})
        return {
            "result": {
                "status": "error",
                "message": f"Không thể tạo đơn hàng: {exc}",
                "error": str(exc),
            }
        }

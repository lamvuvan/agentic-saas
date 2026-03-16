"""Submit order node — sends confirmed order to KiotViet via Tool Registry."""

from __future__ import annotations

import logging

from order_agent.models import OrderDraft
from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError

logger = logging.getLogger(__name__)


async def submit_order(
    draft: OrderDraft,
    tool_registry_url: str,
) -> dict:
    """
    Submit confirmed OrderDraft to KiotViet via Tool Registry.

    Returns result dict with order_code or error.
    """
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
        result = await client.execute("order__create_order", payload)
        order_code = result.get("code") or result.get("order_code") or result.get("id", "")
        logger.info(
            "order_submitted",
            extra={"order_code": order_code, "session_id": draft.session_id},
        )
        return {
            "status": "confirmed",
            "order_code": order_code,
            "message": f"✅ Đơn hàng đã được tạo thành công! Mã đơn: {order_code}",
        }

    except ToolRegistryError as exc:
        logger.error(
            "order_submit_failed",
            extra={"error": str(exc), "session_id": draft.session_id},
        )
        return {
            "status": "error",
            "message": f"Không thể tạo đơn hàng: {exc}",
            "error": str(exc),
        }

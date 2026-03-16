"""Preview node — assembles OrderDraft and formats Vietnamese preview message."""

from __future__ import annotations

import logging

from order_agent.models import OrderDraft, OrderEntities, ProductMatch, ResolvedOrderItem

logger = logging.getLogger(__name__)


def build_preview(
    entities: OrderEntities,
    matches: list[ProductMatch],
    customer_id: str | None,
    customer_name: str | None,
    session_id: str,
) -> tuple[OrderDraft, str]:
    """
    Assemble OrderDraft from matched items and format a Vietnamese preview message.

    Returns:
        (draft, preview_message)
    """
    resolved_items: list[ResolvedOrderItem] = []

    for entity_item, match in zip(entities.items, matches):
        if match.match_status in ("auto", "rerank") and match.matched_product_id:
            resolved_items.append(
                ResolvedOrderItem(
                    product_id=match.matched_product_id,
                    product_name=match.matched_product_name or entity_item.product_query,
                    quantity=entity_item.quantity,
                    price=match.price or 0.0,
                    note=entity_item.note,
                )
            )

    draft = OrderDraft(
        session_id=session_id,
        customer_id=customer_id,
        customer_name=customer_name,
        table_number=entities.table_number,
        items=resolved_items,
        notes=entities.notes,
        status="awaiting_confirm",
    )
    draft.total_estimate = draft.calculate_total()

    preview = _format_preview(draft)
    return draft, preview


def _format_preview(draft: OrderDraft) -> str:
    lines: list[str] = []

    if draft.table_number:
        lines.append(f"📋 Bàn {draft.table_number}")
    if draft.customer_name:
        lines.append(f"👤 Khách: {draft.customer_name}")

    lines.append("\n🍽 Danh sách món:")
    for item in draft.items:
        price_str = f"{item.price:,.0f}đ" if item.price else ""
        note_str = f" ({item.note})" if item.note else ""
        lines.append(f"  • {item.product_name} × {item.quantity}{note_str}  {price_str}")

    if draft.total_estimate is not None:
        lines.append(f"\n💰 Tổng cộng: {draft.total_estimate:,.0f}đ")

    lines.append("\n✅ Xác nhận đơn hàng không? (xác nhận / huỷ)")
    return "\n".join(lines)

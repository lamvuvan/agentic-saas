"""HITL confirm node — classifies user confirmation response (T043).

This node runs AFTER the LangGraph interrupt_before=["hitl_confirm"] fires.
``state["user_confirmation"]`` is populated by Command(resume={"user_confirmation": ...})
before this node executes.

Returns updated state; routing edges after this node direct execution:
    - "confirmed"    → create_order
    - "modify"       → extract_entities  (re-run with updated message)
    - "cancel"       → done
    - "scope_change" → done  (with __scope_change__ flag in result)
"""

from __future__ import annotations

import json
import logging

from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_CLASSIFY_SYSTEM = """Phân loại phản hồi của người dùng sau khi được hỏi xác nhận đơn hàng vào đúng 1 trong 4 loại:
- confirm: Người dùng đồng ý (xác nhận, ok, được, yes, đúng rồi, oke, có)
- cancel: Người dùng từ chối (huỷ, không, thôi, bỏ đi, cancel, thôi bỏ, không cần)
- modify: Người dùng muốn sửa đổi (sửa thành, thay bằng, đổi lại, giảm xuống, thêm, bớt)
- scope_change: Người dùng đổi hoàn toàn sang chủ đề khác

Khi không chắc chắn → chọn cancel.
Trả về JSON: {"intent": "confirm" | "modify" | "cancel" | "scope_change"}"""

_CLASSIFY_SCHEMA = {
    "name": "hitl_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["confirm", "modify", "cancel", "scope_change"]},
        },
        "required": ["intent"],
        "additionalProperties": False,
    },
}


async def _classify_hitl_response(user_input: str) -> str:
    """Classify user HITL response via GPT-4o-mini.

    Returns: "confirm" | "modify" | "cancel" | "scope_change"
    """
    if not user_input:
        return "cancel"

    try:
        raw = await chat_completion_async(
            messages=[{"role": "user", "content": user_input}],
            system=_CLASSIFY_SYSTEM,
            task_type="entity_extract",
            response_schema=_CLASSIFY_SCHEMA,
        )
        data = json.loads(raw)
        return data.get("intent", "cancel")
    except Exception as exc:
        logger.warning("hitl_classify_failed", extra={"error": str(exc)})
        # Fallback: simple keyword match
        text = user_input.lower()
        confirm_words = {"xác nhận", "xac nhan", "ok", "đúng", "oke", "yes", "có", "co", "được"}
        cancel_words = {"huỷ", "huy", "không", "khong", "no", "cancel", "thôi", "thoi", "bỏ"}
        if any(w in text for w in confirm_words):
            return "confirm"
        if any(w in text for w in cancel_words):
            return "cancel"
        return "cancel"


async def hitl_confirm(state: dict, config: dict) -> dict:
    """HITL confirm node — runs after interrupt fires and user responds.

    state["user_confirmation"] must be populated via Command(resume=...) before this runs.
    """
    user_input = state.get("user_confirmation", "")
    intent = await _classify_hitl_response(user_input)

    logger.info(
        "hitl_confirm_classified",
        extra={"intent": intent, "task_id": state.get("task_id", "")},
    )

    if intent == "cancel":
        return {
            "result": {
                "status": "cancelled",
                "message": "Đơn hàng đã huỷ theo yêu cầu của bạn.",
                "__hitl_intent__": "cancel",
            }
        }

    if intent == "scope_change":
        return {
            "result": {
                "__scope_change__": True,
                "new_request": user_input,
                "__hitl_intent__": "scope_change",
            }
        }

    if intent == "modify":
        # Signal modify — extract_entities will re-run with original_message updated
        updated_message = user_input  # user's modification request becomes new message
        return {
            "original_message": updated_message,
            "user_confirmation": "",  # reset for next round
            "entities": {},  # force re-extraction
            "matched_products": [],
            "customer": {},
            "order_preview": {},
            "confirm_message": "",
            "result": {"__hitl_intent__": "modify"},
        }

    # "confirm" — proceed to create_order
    return {"result": {"__hitl_intent__": "confirm"}}

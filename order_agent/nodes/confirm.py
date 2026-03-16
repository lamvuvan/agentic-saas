"""Confirm node — validates user's confirmation input."""

from __future__ import annotations

_CONFIRM_WORDS = frozenset({"xác nhận", "xac nhan", "ok", "đúng", "oke", "yes", "có", "co"})
_CANCEL_WORDS = frozenset({"huỷ", "huy", "không", "khong", "no", "cancel", "thôi", "thoi"})


def check_confirmation(user_input: str) -> str:
    """
    Parse user's confirmation response.

    Returns:
        "confirmed" | "cancelled" | "unknown"
    """
    normalized = user_input.lower().strip()
    if any(word in normalized for word in _CONFIRM_WORDS):
        return "confirmed"
    if any(word in normalized for word in _CANCEL_WORDS):
        return "cancelled"
    return "unknown"

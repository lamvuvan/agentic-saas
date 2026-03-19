"""LangChain callback handler that sends traces to Langfuse."""

from __future__ import annotations

import logging
from typing import Any

from src.observability.langfuse_client import get_langfuse

logger = logging.getLogger(__name__)


def get_langfuse_callback_handler(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    merchant_id: str | None = None,
    trace_name: str = "agent-run",
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    """Create a Langfuse CallbackHandler for LangChain.

    Returns ``None`` if Langfuse is not initialised (graceful degradation).
    """
    langfuse = get_langfuse()
    if langfuse is None:
        return None

    try:
        from langfuse.langchain import CallbackHandler

        # Langfuse 4.x: uses LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY env vars.
        # Per-request trace metadata (session_id, user_id) must be set separately
        # via langfuse.get_client().update_current_trace() if needed.
        handler = CallbackHandler()
        return handler
    except Exception as exc:
        logger.warning("Failed to create Langfuse callback handler: %s", exc)
        return None

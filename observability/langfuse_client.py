"""Langfuse SDK initialisation and singleton access."""

from __future__ import annotations

import logging
from typing import Any

from langfuse import Langfuse

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_langfuse: Langfuse | None = None


def init_langfuse() -> None:
    """Create the Langfuse client (called at app startup)."""
    global _langfuse
    settings = get_settings()
    if not settings.observability.enabled:
        logger.info("Langfuse disabled via config")
        return
    if not settings.observability.langfuse_public_key:
        logger.warning("Langfuse public key not set — tracing disabled")
        return

    _langfuse = Langfuse(
        public_key=settings.observability.langfuse_public_key,
        secret_key=settings.observability.langfuse_secret_key,
        host=settings.observability.langfuse_host,
    )
    logger.info("Langfuse client created (host=%s)", settings.observability.langfuse_host)


def get_langfuse() -> Langfuse | None:
    """Return the Langfuse singleton (may be None if disabled)."""
    return _langfuse


def shutdown_langfuse() -> None:
    """Flush pending events and shut down."""
    global _langfuse
    if _langfuse is not None:
        _langfuse.flush()
        _langfuse.shutdown()
        _langfuse = None

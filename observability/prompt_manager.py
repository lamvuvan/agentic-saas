"""Langfuse Prompt Management wrapper with local fallback."""

from __future__ import annotations

import logging
from typing import Any

from src.observability.langfuse_client import get_langfuse

logger = logging.getLogger(__name__)

# Local prompt cache to reduce Langfuse API calls
_cache: dict[str, str] = {}


def get_prompt(
    name: str,
    *,
    label: str = "production",
    fallback: str = "",
    variables: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> str:
    """Fetch a prompt from Langfuse Prompt Management.

    Args:
        name: Prompt name in Langfuse.
        label: Prompt label (e.g. ``production``, ``staging``, ``latest``).
        fallback: Fallback text if Langfuse is unavailable or prompt not found.
        variables: Template variables to compile into the prompt.
        use_cache: Cache the compiled prompt in memory.

    Returns:
        The compiled prompt string.
    """
    cache_key = f"{name}:{label}"
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    langfuse = get_langfuse()
    if langfuse is None:
        logger.debug("Langfuse not available, using fallback for prompt '%s'", name)
        return fallback

    try:
        prompt_obj = langfuse.get_prompt(name, label=label)
        compiled = prompt_obj.compile(**(variables or {}))
        if use_cache:
            _cache[cache_key] = compiled
        return compiled
    except Exception as exc:
        logger.debug("Failed to fetch prompt '%s' from Langfuse: %s", name, exc)
        return fallback


def invalidate_cache(name: str | None = None) -> None:
    """Clear the prompt cache (all or for a specific prompt name)."""
    if name is None:
        _cache.clear()
    else:
        keys_to_remove = [k for k in _cache if k.startswith(f"{name}:")]
        for k in keys_to_remove:
            del _cache[k]

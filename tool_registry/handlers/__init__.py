"""Handler registry for Python-dispatch tools.

Handlers are registered at import time via register_handler().
The bi_query_handler module auto-registers when imported in main.py.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

HANDLER_REGISTRY: dict[str, Callable[..., Any]] = {}


def register_handler(name: str, fn: Callable[..., Any]) -> None:
    """Register a Python handler function under the given name."""
    HANDLER_REGISTRY[name] = fn


def get_handler(name: str) -> Callable[..., Any] | None:
    """Return the handler callable for the given name, or None if not registered."""
    return HANDLER_REGISTRY.get(name)

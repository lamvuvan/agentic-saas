"""Tool registry — load tool definitions from config/tools.yaml."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from src.core.config import _resolve_env
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "tools.yaml"
_loaded_tools: list[BaseTool] = []


def load_tools() -> list[BaseTool]:
    """Parse ``config/tools.yaml`` and instantiate enabled tools."""
    global _loaded_tools
    if _loaded_tools:
        return _loaded_tools

    if not _CONFIG_PATH.exists():
        logger.warning("tools.yaml not found at %s — no tools loaded", _CONFIG_PATH)
        return []

    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env(raw)

    tools_cfg: list[dict[str, Any]] = raw.get("tools", [])
    loaded: list[BaseTool] = []

    for cfg in tools_cfg:
        if not cfg.get("enabled", True):
            logger.debug("Skipping disabled tool: %s", cfg.get("name"))
            continue

        module_path = cfg["module"]
        class_name = cfg["class"]
        tool_config = cfg.get("config", {})

        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            instance = cls(**tool_config) if tool_config else cls()
            loaded.append(instance)
            logger.info("Loaded tool: %s (%s.%s)", cfg["name"], module_path, class_name)
        except Exception as exc:
            logger.error("Failed to load tool '%s': %s", cfg.get("name"), exc)

    _loaded_tools = loaded
    return _loaded_tools


def get_tools() -> list[BaseTool]:
    """Return previously loaded tools (call ``load_tools`` first at startup)."""
    return _loaded_tools


def get_tool_by_name(name: str) -> BaseTool | None:
    """Lookup a loaded tool by name."""
    for tool in _loaded_tools:
        if tool.name == name:
            return tool
    return None

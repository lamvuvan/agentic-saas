"""Load MCP server definitions from config/mcp_servers.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.core.config import _resolve_env

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent.parent / "config" / "mcp_servers.yaml"


@dataclass
class MCPServerConfig:
    name: str
    description: str = ""
    transport: str = "streamable_http"  # "streamable_http" | "stdio" | "sse" (legacy)
    # stdio transport fields
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    # HTTP transport fields (streamable_http / sse)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = False


def load_mcp_configs() -> list[MCPServerConfig]:
    """Parse ``config/mcp_servers.yaml`` and return enabled server configs."""
    if not _CONFIG_PATH.exists():
        logger.warning("mcp_servers.yaml not found at %s", _CONFIG_PATH)
        return []

    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f) or {}
    raw = _resolve_env(raw)

    configs: list[MCPServerConfig] = []
    for entry in raw.get("mcp_servers", []):
        cfg = MCPServerConfig(
            name=entry.get("name", "unnamed"),
            description=entry.get("description", ""),
            transport=entry.get("transport", "stdio"),
            command=entry.get("command", ""),
            args=entry.get("args", []),
            env=entry.get("env", {}),
            url=entry.get("url", ""),
            headers=entry.get("headers", {}),
            enabled=entry.get("enabled", False),
        )
        if cfg.enabled:
            configs.append(cfg)

    return configs

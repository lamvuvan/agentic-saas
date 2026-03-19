"""MCP Client adapter — connects to MCP servers and exposes their tools as LangChain tools.

Transport support
-----------------
``streamable_http``  — recommended for all HTTP-based MCP servers (MCP spec ≥ 1.0).
                       Replaces the older ``sse`` transport.
``sse``              — legacy HTTP transport, kept for backward compatibility.
``stdio``            — subprocess-based local MCP servers.

Reference: https://docs.langchain.com/langsmith/server-mcp
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool as LCBaseTool

from src.tools.mcp.config import MCPServerConfig, load_mcp_configs

logger = logging.getLogger(__name__)

# Stores active MCP tool references
_mcp_tools: list[LCBaseTool] = []
_mcp_clients: list[Any] = []


async def init_mcp_clients() -> None:
    """Initialise MCP client connections for all enabled servers.

    Uses ``langchain-mcp-adapters`` (``MultiServerMCPClient``) to convert
    MCP tool definitions into LangChain-compatible tools.
    """
    global _mcp_tools, _mcp_clients
    configs = load_mcp_configs()

    if not configs:
        logger.info("No enabled MCP servers configured")
        return

    for cfg in configs:
        try:
            tools = await _connect_server(cfg)
            _mcp_tools.extend(tools)
            logger.info(
                "MCP server '%s' (%s) connected: %d tools",
                cfg.name, cfg.transport, len(tools),
            )
        except Exception as exc:
            logger.error("Failed to connect MCP server '%s': %s", cfg.name, exc)


async def _connect_server(cfg: MCPServerConfig) -> list[LCBaseTool]:
    """Connect to a single MCP server and return its tools as LangChain tools."""
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError:
        logger.warning(
            "langchain-mcp-adapters not installed. "
            "Install with: pip install langchain-mcp-adapters"
        )
        return []

    if cfg.transport == "stdio":
        server_params = {
            cfg.name: {
                "command": cfg.command,
                "args": cfg.args,
                "env": cfg.env or None,
                "transport": "stdio",
            }
        }

    elif cfg.transport == "streamable_http":
        # Recommended transport for HTTP-based MCP servers (MCP spec ≥ 1.0)
        server_params = {
            cfg.name: {
                "transport": "streamable_http",
                "url": cfg.url,
                "headers": cfg.headers or {},
            }
        }

    elif cfg.transport == "sse":
        # Legacy SSE transport — use streamable_http for new servers
        logger.warning(
            "MCP server '%s' uses deprecated 'sse' transport. "
            "Consider upgrading to 'streamable_http'.",
            cfg.name,
        )
        server_params = {
            cfg.name: {
                "transport": "sse",
                "url": cfg.url,
                "headers": cfg.headers or {},
            }
        }

    else:
        logger.warning(
            "Unknown MCP transport '%s' for server '%s'. "
            "Supported: streamable_http, stdio, sse",
            cfg.transport, cfg.name,
        )
        return []

    client = MultiServerMCPClient(server_params)
    _mcp_clients.append(client)
    tools = await client.get_tools()
    return tools


async def close_mcp_clients() -> None:
    """Clean up MCP client connections."""
    global _mcp_tools, _mcp_clients
    for client in _mcp_clients:
        try:
            if hasattr(client, "aclose"):
                await client.aclose()
            elif hasattr(client, "close"):
                await client.close()
        except Exception as exc:
            logger.debug("Error closing MCP client: %s", exc)
    _mcp_tools.clear()
    _mcp_clients.clear()


def get_mcp_tools() -> list[LCBaseTool]:
    """Return all MCP-provided tools (available after ``init_mcp_clients``)."""
    return _mcp_tools

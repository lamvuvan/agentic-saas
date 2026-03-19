"""Expose K-Agentic orchestrator tools as an MCP server endpoint.

Mounting this at ``/mcp`` in the main FastAPI app makes the orchestrator's
tools discoverable by any MCP-compatible client (other agents, IDEs, etc.).

Usage
-----
Any MCP client can connect to ``http://your-host:8000/mcp`` using the
``streamable_http`` transport::

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({
        "k_agentic": {
            "transport": "streamable_http",
            "url": "http://localhost:8000/mcp",
            "headers": {"Authorization": "Bearer <api_key>"},
        }
    })
    tools = await client.get_tools()

Requirements
------------
``pip install mcp``  (the official Python MCP SDK, provides FastMCP)

Reference: https://docs.langchain.com/langsmith/server-mcp
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_mcp_app() -> Any:
    """Build and return a FastMCP ASGI application.

    Returns ``None`` (with a warning) if the ``mcp`` library is not installed.
    Mount the returned app at ``/mcp`` in the main FastAPI instance::

        mcp_app = create_mcp_app()
        if mcp_app:
            application.mount("/mcp", mcp_app)
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        logger.warning(
            "mcp package not installed — MCP server endpoint disabled. "
            "Install with: pip install mcp"
        )
        return None

    from src.tools.registry import get_tools

    mcp = FastMCP(
        name="K-Agentic",
        instructions=(
            "K-Agentic orchestrator tools — web search, API caller, "
            "and domain-specific utilities."
        ),
    )

    # Register each tool from the registry as an MCP tool.
    # Tools are loaded lazily so this must be called after the registry is
    # initialised (i.e. from a lifespan handler, not at import time).
    for tool in get_tools():
        _register_tool(mcp, tool)

    logger.info("MCP server configured with %d tools", len(get_tools()))
    return mcp.streamable_http_app()


def _register_tool(mcp: Any, tool: Any) -> None:
    """Register a single LangChain tool with the FastMCP instance."""
    tool_name = getattr(tool, "name", None)
    tool_desc = getattr(tool, "description", "")

    if not tool_name:
        return

    # Build a dynamic async handler that delegates to the LangChain tool.
    # FastMCP will infer the schema from the function signature / docstring.
    async def _handler(**kwargs: Any) -> str:  # type: ignore[misc]
        try:
            return await tool._arun(**kwargs)
        except Exception as exc:
            return f"[Tool error] {exc}"

    _handler.__name__ = tool_name
    _handler.__doc__ = tool_desc

    try:
        mcp.tool(name=tool_name, description=tool_desc)(_handler)
    except Exception as exc:
        logger.warning("Could not register tool '%s' with MCP: %s", tool_name, exc)

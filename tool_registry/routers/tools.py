"""GET /tools and POST /tools/{name}/execute endpoints."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tool_registry.models import ExecutionResponse, ToolDefinition

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_store():
    """Return the active ToolStore. Overridable in tests via monkeypatching."""
    from tool_registry import main as _main

    loader = getattr(_main, "_config_loader", None)
    if loader is None:
        raise RuntimeError("Store not initialised — is the lifespan running?")
    return loader.store


def to_openai_schema(tool: ToolDefinition) -> dict[str, Any]:
    """Convert a ToolDefinition to OpenAI function-call schema format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


@router.get("/tools", response_model=list[dict])
async def list_tools(namespace: str | None = None) -> list[dict[str, Any]]:
    """Return all registered tools in OpenAI function-call format.

    Optionally filter by namespace (e.g., ?namespace=customer).
    """
    store = _get_store()
    tools = store.list(namespace=namespace)
    return [to_openai_schema(t) for t in tools]


@router.post("/tools/{name}/execute")
async def execute_tool(name: str, request: Request) -> JSONResponse:
    """Execute a named tool and return the normalized result.

    Bearer token is forwarded via ContextVar — never stored or logged.
    """
    start = time.monotonic()
    trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))

    store = _get_store()

    # Look up tool
    tool = store.get(name)
    if tool is None:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("tool_not_found trace_id=%s tool=%s", trace_id, name)
        return JSONResponse(
            status_code=404,
            content=ExecutionResponse(
                tool_name=name,
                error=f"Tool '{name}' not found in registry",
                error_code="TOOL_NOT_FOUND",
                metadata={"duration_ms": duration_ms},
            ).model_dump(),
        )

    # Parse request body
    try:
        body = await request.json()
        params = body if isinstance(body, dict) else {}
    except Exception:
        params = {}

    # Dispatch
    from tool_registry.dispatch import dispatch

    result = await dispatch(tool, params)

    duration_ms = int((time.monotonic() - start) * 1000)
    result.metadata["duration_ms"] = duration_ms

    status_code = 200
    if result.error_code == "TOOL_NOT_FOUND":
        status_code = 404
    elif result.error_code == "TIMEOUT":
        status_code = 504
    elif result.error_code in ("BACKEND_ERROR", "HANDLER_ERROR"):
        status_code = 502
    elif result.error_code == "VALIDATION_ERROR":
        status_code = 422

    # Log execution (token intentionally excluded)
    logger.info(
        "tool_execute trace_id=%s tool=%s status=%s duration_ms=%d",
        trace_id,
        name,
        result.error_code or "ok",
        duration_ms,
    )

    return JSONResponse(status_code=status_code, content=result.model_dump())

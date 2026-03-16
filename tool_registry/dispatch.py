"""Tool dispatch — routes execution to HTTP adapter or Python handler.

Security: Bearer token is read from ContextVar and forwarded to backends.
It is NEVER logged, stored, or included in error responses.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any

import httpx

from tool_registry.models import ApiBlock, ExecutionResponse, HandlerRef, ToolDefinition

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AuthContext:
    token: str
    tenant_id: str


# ---------------------------------------------------------------------------
# HTTP dispatch
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = 10.0
_HTTP_MAX_RETRIES = 2


def _build_request_kwargs(tool: ToolDefinition, params: dict[str, Any]) -> dict[str, Any]:
    """Build httpx request kwargs from ApiBlock and caller params."""
    api: ApiBlock = tool.dispatch  # type: ignore[assignment]
    kwargs: dict[str, Any] = {"url": api.url, "timeout": _HTTP_TIMEOUT}

    if api.method == "GET":
        # Map tool params → backend query params
        if api.params:
            kwargs["params"] = {
                backend_key: params[tool_key]
                for tool_key, backend_key in api.params.items()
                if tool_key in params
            }
        else:
            kwargs["params"] = {k: v for k, v in params.items()}
    else:
        # Map tool params → POST/PUT body
        if api.body_mapping:
            kwargs["json"] = {
                backend_key: params[tool_key]
                for tool_key, backend_key in api.body_mapping.items()
                if tool_key in params
            }
        else:
            kwargs["json"] = params

    return kwargs


def _normalize_response(api: ApiBlock, raw: Any) -> Any:
    """Apply response_path, response_rename, response_fields normalization."""
    data = raw

    # Extract nested path
    if api.response_path and isinstance(data, dict):
        data = data.get(api.response_path, data)

    # Rename fields
    if api.response_rename:
        if isinstance(data, list):
            data = [
                {api.response_rename.get(k, k): v for k, v in item.items()}
                if isinstance(item, dict)
                else item
                for item in data
            ]
        elif isinstance(data, dict):
            data = {api.response_rename.get(k, k): v for k, v in data.items()}

    # Whitelist fields
    if api.response_fields:
        fields = set(api.response_fields)
        if isinstance(data, list):
            data = [
                {k: v for k, v in item.items() if k in fields}
                if isinstance(item, dict)
                else item
                for item in data
            ]
        elif isinstance(data, dict):
            data = {k: v for k, v in data.items() if k in fields}

    return data


async def execute_http(tool: ToolDefinition, params: dict[str, Any], auth: AuthContext) -> ExecutionResponse:
    """Execute an HTTP-dispatched tool, retrying on transport errors."""
    api: ApiBlock = tool.dispatch  # type: ignore[assignment]

    headers: dict[str, str] = {}
    if auth.token:
        headers["Authorization"] = f"Bearer {auth.token}"
    if auth.tenant_id:
        headers["X-Tenant-Id"] = auth.tenant_id

    request_kwargs = _build_request_kwargs(tool, params)
    last_exc: Exception | None = None

    async with httpx.AsyncClient(headers=headers) as client:
        for attempt in range(1, _HTTP_MAX_RETRIES + 2):  # 1 initial + 2 retries
            try:
                response = await client.request(api.method, **request_kwargs)
                break
            except httpx.TimeoutException as exc:
                raise  # Timeout is not retried — propagate immediately
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt <= _HTTP_MAX_RETRIES:
                    logger.warning(
                        "tool_dispatch_retry attempt=%d tool=%s error=%s",
                        attempt,
                        tool.name,
                        type(exc).__name__,
                    )
                    continue
                raise
        else:
            # All retries exhausted (TransportError path)
            raise last_exc  # type: ignore[misc]

    backend_status = response.status_code

    if not response.is_success:
        return ExecutionResponse(
            tool_name=tool.name,
            error=f"Backend returned HTTP {backend_status}",
            error_code="BACKEND_ERROR",
            metadata={"backend_status": backend_status},
        )

    try:
        raw = response.json()
    except Exception:
        raw = response.text

    result = _normalize_response(api, raw)

    return ExecutionResponse(
        tool_name=tool.name,
        result=result,
        metadata={"backend_status": backend_status},
    )


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


async def execute_handler(tool: ToolDefinition, params: dict[str, Any], auth: AuthContext) -> ExecutionResponse:
    """Execute a Python handler registered in HANDLER_REGISTRY."""
    from tool_registry.handlers import get_handler

    ref: HandlerRef = tool.dispatch  # type: ignore[assignment]
    handler = get_handler(ref.name)

    if handler is None:
        return ExecutionResponse(
            tool_name=tool.name,
            error=f"Handler '{ref.name}' not found in registry",
            error_code="HANDLER_ERROR",
            metadata={"backend_status": 0},
        )

    result = await handler(params, auth)

    return ExecutionResponse(
        tool_name=tool.name,
        result=result,
        metadata={"backend_status": 0},
    )


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------


async def dispatch(tool: ToolDefinition, params: dict[str, Any]) -> ExecutionResponse:
    """Route to HTTP or handler dispatch based on tool's dispatch type.

    Reads auth from ContextVars — token is NEVER logged.
    """
    from shared.auth_context import get_tenant_id, get_token

    auth = AuthContext(token=get_token(), tenant_id=get_tenant_id())

    try:
        if isinstance(tool.dispatch, ApiBlock):
            return await execute_http(tool, params, auth)
        else:
            return await execute_handler(tool, params, auth)

    except httpx.TimeoutException:
        logger.warning("tool_dispatch_timeout tool=%s", tool.name)
        return ExecutionResponse(
            tool_name=tool.name,
            error=f"Tool '{tool.name}' timed out after {_HTTP_TIMEOUT}s",
            error_code="TIMEOUT",
            metadata={},
        )
    except httpx.TransportError as exc:
        logger.warning("tool_dispatch_network_error tool=%s error=%s", tool.name, type(exc).__name__)
        return ExecutionResponse(
            tool_name=tool.name,
            error=f"Network error executing '{tool.name}': {type(exc).__name__}",
            error_code="BACKEND_ERROR",
            metadata={},
        )
    except Exception as exc:
        logger.error("tool_dispatch_handler_error tool=%s error=%s", tool.name, type(exc).__name__)
        return ExecutionResponse(
            tool_name=tool.name,
            error=f"Handler error for '{tool.name}': {exc}",
            error_code="HANDLER_ERROR",
            metadata={},
        )

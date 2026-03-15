"""T024: Unit tests for dispatch.py — routing, timeout, retries, token forwarding."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tool_registry.models import ApiBlock, ExecutionResponse, HandlerRef, ToolDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_tool(name="customer__get_customers", method="GET", url="https://api.example.com/customers"):
    return ToolDefinition(
        name=name,
        namespace=name.split("__")[0],
        description="Test tool",
        parameters={"type": "object", "properties": {}},
        dispatch=ApiBlock(method=method, url=url),
    )


def _make_handler_tool(name="bi__run_query", handler_name="bi_query_handler"):
    return ToolDefinition(
        name=name,
        namespace=name.split("__")[0],
        description="Test handler tool",
        parameters={"type": "object", "properties": {"sql": {"type": "string"}}},
        dispatch=HandlerRef(name=handler_name),
    )


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    @pytest.mark.asyncio
    async def test_api_block_tool_routes_to_http_path(self):
        """ApiBlock dispatch should call execute_http, not execute_handler."""
        tool = _make_api_tool()

        with patch("tool_registry.dispatch.execute_http", new_callable=AsyncMock) as mock_http:
            mock_http.return_value = ExecutionResponse(
                tool_name=tool.name,
                result=[],
                metadata={"backend_status": 200},
            )
            from tool_registry.dispatch import dispatch

            result = await dispatch(tool, {})

        mock_http.assert_awaited_once()
        assert result.tool_name == tool.name

    @pytest.mark.asyncio
    async def test_handler_ref_tool_routes_to_handler_path(self):
        """HandlerRef dispatch should call execute_handler, not execute_http."""
        tool = _make_handler_tool()

        with patch("tool_registry.dispatch.execute_handler", new_callable=AsyncMock) as mock_handler:
            mock_handler.return_value = ExecutionResponse(
                tool_name=tool.name,
                result={"rows": []},
                metadata={"backend_status": 0},
            )
            from tool_registry.dispatch import dispatch

            result = await dispatch(tool, {"sql": "SELECT 1"})

        mock_handler.assert_awaited_once()
        assert result.tool_name == tool.name


# ---------------------------------------------------------------------------
# Timeout tests
# ---------------------------------------------------------------------------


class TestDispatchTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_timeout_error_code(self):
        """Network timeout must produce TIMEOUT error_code."""
        tool = _make_api_tool()

        with patch("tool_registry.dispatch.execute_http", new_callable=AsyncMock) as mock_http:
            mock_http.side_effect = httpx.TimeoutException("timed out")
            from tool_registry.dispatch import dispatch

            result = await dispatch(tool, {})

        assert result.error_code == "TIMEOUT"
        assert result.error is not None
        assert "timeout" in result.error.lower() or "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_result_is_none(self):
        tool = _make_api_tool()

        with patch("tool_registry.dispatch.execute_http", new_callable=AsyncMock) as mock_http:
            mock_http.side_effect = httpx.TimeoutException("timed out")
            from tool_registry.dispatch import dispatch

            result = await dispatch(tool, {})

        assert result.result is None


# ---------------------------------------------------------------------------
# Token forwarding tests
# ---------------------------------------------------------------------------


class TestDispatchTokenForwarding:
    @pytest.mark.asyncio
    async def test_token_from_contextvar_forwarded_in_auth_header(self):
        """Authorization header must contain the token set via ContextVar."""
        tool = _make_api_tool()
        captured_headers = {}

        async def fake_execute_http(tool, params, auth):
            captured_headers.update({"Authorization": f"Bearer {auth.token}"})
            return ExecutionResponse(
                tool_name=tool.name,
                result=[],
                metadata={"backend_status": 200},
            )

        from shared.auth_context import set_auth

        set_auth("my-secret-token", "tenant-1")

        with patch("tool_registry.dispatch.execute_http", side_effect=fake_execute_http):
            from tool_registry.dispatch import dispatch

            await dispatch(tool, {})

        assert captured_headers.get("Authorization") == "Bearer my-secret-token"

    @pytest.mark.asyncio
    async def test_token_not_in_log_output(self, caplog):
        """Bearer token must NOT appear in any log output."""
        tool = _make_api_tool()
        secret_token = "super-secret-bearer-xyz"

        from shared.auth_context import set_auth

        set_auth(secret_token, "tenant-1")

        with patch("tool_registry.dispatch.execute_http", new_callable=AsyncMock) as mock_http:
            mock_http.return_value = ExecutionResponse(
                tool_name=tool.name,
                result=[],
                metadata={"backend_status": 200},
            )
            from tool_registry.dispatch import dispatch

            with caplog.at_level(logging.DEBUG, logger="tool_registry"):
                await dispatch(tool, {})

        for record in caplog.records:
            assert secret_token not in record.getMessage(), (
                f"Token leaked in log record: {record.getMessage()}"
            )


# ---------------------------------------------------------------------------
# Handler error tests
# ---------------------------------------------------------------------------


class TestDispatchHandlerError:
    @pytest.mark.asyncio
    async def test_handler_exception_returns_handler_error_code(self):
        """Unhandled exception from handler should map to HANDLER_ERROR."""
        tool = _make_handler_tool()

        with patch("tool_registry.dispatch.execute_handler", new_callable=AsyncMock) as mock_handler:
            mock_handler.side_effect = RuntimeError("DB connection failed")
            from tool_registry.dispatch import dispatch

            result = await dispatch(tool, {"sql": "SELECT 1"})

        assert result.error_code == "HANDLER_ERROR"
        assert result.error is not None

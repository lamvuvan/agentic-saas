"""T032: Unit tests for ToolRegistryClient."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError


@pytest.fixture
def client():
    return ToolRegistryClient(base_url="http://localhost:8001")


class TestGetOpenaiTools:
    @pytest.mark.asyncio
    async def test_returns_list_with_function_type(self, client, respx_mock):
        """get_openai_tools() returns list where each item has type='function'."""
        respx_mock.get("http://localhost:8001/tools").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "type": "function",
                        "function": {
                            "name": "customer__get_customers",
                            "description": "Find customers",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )
        )

        result = await client.get_openai_tools()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "function"

    @pytest.mark.asyncio
    async def test_namespace_filter_passes_query_param(self, client, respx_mock):
        """get_openai_tools(namespace='bi') passes ?namespace=bi query param."""
        respx_mock.get("http://localhost:8001/tools", params={"namespace": "bi"}).mock(
            return_value=httpx.Response(200, json=[])
        )

        result = await client.get_openai_tools(namespace="bi")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_namespace_no_query_param(self, client, respx_mock):
        """get_openai_tools() without namespace does not send ?namespace= param."""
        route = respx_mock.get("http://localhost:8001/tools").mock(
            return_value=httpx.Response(200, json=[])
        )

        await client.get_openai_tools()
        assert route.called


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_sets_authorization_header_from_contextvar(self, client, respx_mock):
        """execute() reads token from ContextVar and sets Authorization: Bearer <token>."""
        from shared.auth_context import set_auth

        set_auth("client-bearer-token", "tenant-abc")

        captured_headers = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers["Authorization"] = request.headers.get("Authorization", "")
            return httpx.Response(
                200,
                json={
                    "tool_name": "customer__get_customers",
                    "result": [],
                    "metadata": {"backend_status": 200, "duration_ms": 10},
                },
            )

        respx_mock.post("http://localhost:8001/tools/customer__get_customers/execute").mock(
            side_effect=capture
        )

        await client.execute("customer__get_customers", {"query": "test"})
        assert captured_headers.get("Authorization") == "Bearer client-bearer-token"

    @pytest.mark.asyncio
    async def test_execute_returns_result_field(self, client, respx_mock):
        """execute() returns the 'result' field from the registry response."""
        respx_mock.post("http://localhost:8001/tools/customer__get_customers/execute").mock(
            return_value=httpx.Response(
                200,
                json={
                    "tool_name": "customer__get_customers",
                    "result": [{"id": "1", "name": "Test"}],
                    "metadata": {"backend_status": 200, "duration_ms": 10},
                },
            )
        )

        result = await client.execute("customer__get_customers", {})
        assert result == [{"id": "1", "name": "Test"}]

    @pytest.mark.asyncio
    async def test_execute_raises_tool_registry_error_on_404(self, client, respx_mock):
        """execute() raises ToolRegistryError with error_code when registry returns 404."""
        respx_mock.post("http://localhost:8001/tools/missing__tool/execute").mock(
            return_value=httpx.Response(
                404,
                json={
                    "tool_name": "missing__tool",
                    "error": "Tool 'missing__tool' not found in registry",
                    "error_code": "TOOL_NOT_FOUND",
                    "metadata": {"duration_ms": 1},
                },
            )
        )

        with pytest.raises(ToolRegistryError) as exc_info:
            await client.execute("missing__tool", {})

        assert exc_info.value.error_code == "TOOL_NOT_FOUND"
        assert exc_info.value.tool_name == "missing__tool"

    @pytest.mark.asyncio
    async def test_execute_raises_on_connection_error(self, client, respx_mock):
        """execute() raises ToolRegistryError on connection failure."""
        respx_mock.post("http://localhost:8001/tools/customer__get_customers/execute").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        with pytest.raises((ToolRegistryError, httpx.ConnectError)):
            await client.execute("customer__get_customers", {})

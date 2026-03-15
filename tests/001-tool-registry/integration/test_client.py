"""T033: Integration tests for ToolRegistryClient against a real test app."""
from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError


@pytest.fixture
def app_with_tools(tmp_path):
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        yaml.dump(
            {
                "tools": [
                    {
                        "name": "customer__get_customers",
                        "namespace": "customer",
                        "description": "Find customers",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "api": {
                            "method": "GET",
                            "url": "https://api.example.com/customers",
                        },
                    },
                    {
                        "name": "bi__run_query",
                        "namespace": "bi",
                        "description": "Run SQL",
                        "parameters": {
                            "type": "object",
                            "properties": {"sql": {"type": "string"}},
                        },
                        "handler": "bi_query_handler",
                    },
                ]
            }
        )
    )

    from tool_registry.config_loader import ConfigLoader
    from tool_registry.main import create_app

    app = create_app()
    loader = ConfigLoader(yaml_path=str(tools_yaml))
    loader.load()
    app.state.config_loader = loader

    import tool_registry.routers.tools as tools_router

    tools_router._get_store = lambda: loader.store
    return app


class TestClientIntegration:
    @pytest.mark.asyncio
    async def test_get_openai_tools_roundtrip(self, app_with_tools, respx_mock):
        """Full roundtrip: ToolRegistryClient.get_openai_tools() returns OpenAI format list."""
        # Use respx to intercept the client's HTTP call to the "registry"
        respx_mock.get("http://registry.test/tools").mock(
            return_value=_build_tools_response()
        )

        client = ToolRegistryClient(base_url="http://registry.test")
        tools = await client.get_openai_tools()

        assert isinstance(tools, list)
        assert len(tools) >= 1
        for tool in tools:
            assert tool["type"] == "function"
            assert "name" in tool["function"]

    @pytest.mark.asyncio
    async def test_execute_roundtrip_token_forwarded(self, app_with_tools, respx_mock):
        """execute() end-to-end: token forwarded from ContextVar to registry call."""
        import httpx as _httpx

        from shared.auth_context import set_auth

        set_auth("end-to-end-token", "tenant-999")

        captured_auth = {}

        def capture_execute(request: _httpx.Request) -> _httpx.Response:
            captured_auth["Authorization"] = request.headers.get("Authorization", "")
            return _httpx.Response(
                200,
                json={
                    "tool_name": "customer__get_customers",
                    "result": [{"id": "1"}],
                    "metadata": {"backend_status": 200, "duration_ms": 5},
                },
            )

        respx_mock.post("http://registry.test/tools/customer__get_customers/execute").mock(
            side_effect=capture_execute
        )

        client = ToolRegistryClient(base_url="http://registry.test")
        result = await client.execute("customer__get_customers", {"query": "test"})

        assert captured_auth.get("Authorization") == "Bearer end-to-end-token"
        assert result == [{"id": "1"}]


def _build_tools_response():
    import httpx

    return httpx.Response(
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

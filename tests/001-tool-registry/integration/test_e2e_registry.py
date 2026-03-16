"""T038: End-to-end integration tests for Tool Registry — all namespaces, all endpoints."""
from __future__ import annotations

import pytest
import httpx
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def full_app(tmp_path):
    """App loaded with all 3 namespaces from the main tools.yaml structure."""
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        yaml.dump(
            {
                "tools": [
                    {
                        "name": "customer__get_customers",
                        "namespace": "customer",
                        "description": "Find customers by name or phone",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "api": {"method": "GET", "url": "https://api.example.com/customers"},
                    },
                    {
                        "name": "customer__create_customer",
                        "namespace": "customer",
                        "description": "Create a new customer",
                        "parameters": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "phone": {"type": "string"}},
                        },
                        "api": {"method": "POST", "url": "https://api.example.com/customers"},
                    },
                    {
                        "name": "order__create_order",
                        "namespace": "order",
                        "description": "Create a new order",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "customer_id": {"type": "string"},
                                "items": {"type": "array"},
                            },
                        },
                        "api": {"method": "POST", "url": "https://api.example.com/orders"},
                    },
                    {
                        "name": "bi__run_query",
                        "namespace": "bi",
                        "description": "Run a SQL query",
                        "parameters": {
                            "type": "object",
                            "required": ["sql"],
                            "properties": {"sql": {"type": "string"}, "limit": {"type": "integer"}},
                        },
                        "handler": "bi_query_handler",
                    },
                ]
            }
        )
    )

    from tool_registry.config_loader import ConfigLoader
    from tool_registry.main import create_app

    import tool_registry.handlers.bi_query_handler  # noqa: F401 — auto-register

    app = create_app()
    loader = ConfigLoader(yaml_path=str(tools_yaml))
    loader.load()

    import tool_registry.routers.tools as tools_router

    with TestClient(app, raise_server_exceptions=True) as c:
        app.state.config_loader = loader
        tools_router._get_store = lambda: loader.store
        yield c


class TestE2ERegistry:
    def test_get_tools_returns_all_tools_openai_format(self, full_app):
        """GET /tools returns all 4 tools in OpenAI function-call format."""
        resp = full_app.get("/tools")
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 4
        names = [t["function"]["name"] for t in tools]
        assert "customer__get_customers" in names
        assert "customer__create_customer" in names
        assert "order__create_order" in names
        assert "bi__run_query" in names
        for t in tools:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "description" in t["function"]
            assert "parameters" in t["function"]

    def test_get_tools_customer_namespace_returns_2(self, full_app):
        resp = full_app.get("/tools?namespace=customer")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_tools_order_namespace_returns_1(self, full_app):
        resp = full_app.get("/tools?namespace=order")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_tools_bi_namespace_returns_1(self, full_app):
        resp = full_app.get("/tools?namespace=bi")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_execute_customer_tool_mocked(self, full_app, respx_mock):
        """Execute customer tool — respx mocks the backend."""
        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(200, json=[{"id": "1", "name": "Test"}])
        )
        resp = full_app.post(
            "/tools/customer__get_customers/execute",
            json={"query": "Test"},
            headers={"Authorization": "Bearer e2e-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_name"] == "customer__get_customers"
        assert data["metadata"]["backend_status"] == 200

    def test_execute_order_tool_mocked(self, full_app, respx_mock):
        """Execute order tool — respx mocks the backend."""
        respx_mock.post("https://api.example.com/orders").mock(
            return_value=httpx.Response(201, json={"order_id": "ord-001"})
        )
        resp = full_app.post(
            "/tools/order__create_order/execute",
            json={"customer_id": "1", "items": []},
        )
        assert resp.status_code == 200

    def test_execute_unknown_tool_returns_404(self, full_app):
        resp = full_app.post("/tools/unknown__tool/execute", json={})
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TOOL_NOT_FOUND"

    def test_health_reports_correct_tool_count(self, full_app):
        """GET /health returns tool_count=4 matching loaded tools."""
        resp = full_app.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["tool_count"] == 4

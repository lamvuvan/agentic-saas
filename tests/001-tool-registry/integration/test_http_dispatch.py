"""T030: Integration tests for HTTP dispatch via respx mock."""
from __future__ import annotations

import pytest
import httpx
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_customer_tool(tmp_path):
    """FastAPI test app with a customer tool that has response_rename configured."""
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
                            "response_path": "data",
                            "response_rename": {"customerId": "customer_id", "fullName": "name"},
                        },
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


class TestHttpDispatchIntegration:
    def test_customer_tool_returns_mocked_response(self, app_with_customer_tool, respx_mock):
        """Successful execution returns normalized result from mock backend."""
        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"customerId": "1", "fullName": "Lâm Nguyễn"}]},
            )
        )

        with TestClient(app_with_customer_tool, raise_server_exceptions=True) as client:
            resp = client.post(
                "/tools/customer__get_customers/execute",
                json={"query": "Lâm"},
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_name"] == "customer__get_customers"
        assert data["metadata"]["backend_status"] == 200

    def test_response_rename_applied(self, app_with_customer_tool, respx_mock):
        """response_rename mapping transforms backend field names."""
        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"customerId": "42", "fullName": "Test User"}]},
            )
        )

        with TestClient(app_with_customer_tool, raise_server_exceptions=True) as client:
            resp = client.post("/tools/customer__get_customers/execute", json={})

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert isinstance(result, list)
        # Keys should be renamed from backend names to tool names
        assert result[0].get("customer_id") == "42"
        assert result[0].get("name") == "Test User"
        assert "customerId" not in result[0]

    def test_authorization_header_forwarded_to_backend(self, app_with_customer_tool, respx_mock):
        """Authorization header from client must be forwarded to backend."""
        captured_headers = {}

        def capture_request(request: httpx.Request) -> httpx.Response:
            captured_headers["Authorization"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json={"data": []})

        respx_mock.get("https://api.example.com/customers").mock(side_effect=capture_request)

        with TestClient(app_with_customer_tool, raise_server_exceptions=True) as client:
            client.post(
                "/tools/customer__get_customers/execute",
                json={},
                headers={"Authorization": "Bearer forwarded-token"},
            )

        assert captured_headers.get("Authorization") == "Bearer forwarded-token"

    def test_timeout_mock_triggers_timeout_error_code(self, app_with_customer_tool, respx_mock):
        """When backend times out, response must have TIMEOUT error_code and HTTP 504."""
        respx_mock.get("https://api.example.com/customers").mock(
            side_effect=httpx.ReadTimeout("timed out", request=None)
        )

        with TestClient(app_with_customer_tool, raise_server_exceptions=False) as client:
            resp = client.post("/tools/customer__get_customers/execute", json={})

        assert resp.status_code == 504
        data = resp.json()
        assert data["error_code"] == "TIMEOUT"
        assert data["result"] is None

    def test_backend_error_returns_502(self, app_with_customer_tool, respx_mock):
        """When backend returns 500, registry returns 502 BACKEND_ERROR."""
        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(500, json={"error": "Internal server error"})
        )

        with TestClient(app_with_customer_tool, raise_server_exceptions=False) as client:
            resp = client.post("/tools/customer__get_customers/execute", json={})

        assert resp.status_code == 502
        assert resp.json()["error_code"] == "BACKEND_ERROR"

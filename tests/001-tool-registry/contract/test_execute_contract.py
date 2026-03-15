"""T023: Contract test — POST /tools/{name}/execute response matches execute-tool.json schema."""
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

CONTRACT = json.loads(
    (Path(__file__).parents[3] / "specs/001-tool-registry/contracts/execute-tool.json").read_text()
)


@pytest.fixture
def client(tmp_path):
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        yaml.dump(
            {
                "tools": [
                    {
                        "name": "customer__get_customers",
                        "namespace": "customer",
                        "description": "Find customers by name or phone number.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                        },
                        "api": {
                            "method": "GET",
                            "url": "https://api.example.com/customers",
                        },
                    },
                ]
            }
        )
    )

    from tool_registry.config_loader import ConfigLoader
    from tool_registry.main import create_app

    app = create_app()
    from tool_registry import main as main_module

    loader = ConfigLoader(yaml_path=str(tools_yaml))
    loader.load()
    app.state.config_loader = loader

    import tool_registry.routers.tools as tools_router

    tools_router._get_store = lambda: loader.store

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestExecuteContractSuccess:
    def test_200_has_required_fields(self, client, respx_mock):
        """200 response must have tool_name, result, metadata with duration_ms and backend_status."""
        import respx
        import httpx

        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(200, json=[{"customer_id": "1", "name": "Test"}])
        )

        resp = client.post(
            "/tools/customer__get_customers/execute",
            json={"query": "Test"},
            headers={"Authorization": "Bearer testtoken"},
        )
        assert resp.status_code == 200
        data = resp.json()

        schema = CONTRACT["response"]["200"]["schema"]
        for field in schema["required"]:
            assert field in data, f"Missing required field: {field}"

        assert "duration_ms" in data["metadata"]
        assert "backend_status" in data["metadata"]
        assert data["tool_name"] == "customer__get_customers"

    def test_200_result_is_object_or_array(self, client, respx_mock):
        import httpx

        respx_mock.get("https://api.example.com/customers").mock(
            return_value=httpx.Response(200, json=[])
        )

        resp = client.post(
            "/tools/customer__get_customers/execute",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["result"], (dict, list))


class TestExecuteContractNotFound:
    def test_404_has_required_fields(self, client):
        """404 response must have tool_name, error, error_code=TOOL_NOT_FOUND."""
        resp = client.post(
            "/tools/nonexistent__tool/execute",
            json={},
        )
        assert resp.status_code == 404
        data = resp.json()

        schema = CONTRACT["response"]["404"]["schema"]
        for field in schema["required"]:
            assert field in data, f"Missing required field: {field}"

        assert data["error_code"] == "TOOL_NOT_FOUND"
        assert isinstance(data["error"], str)
        assert len(data["error"]) > 0

    def test_404_tool_name_echoed(self, client):
        resp = client.post("/tools/missing__tool/execute", json={})
        assert resp.status_code == 404
        assert resp.json()["tool_name"] == "missing__tool"

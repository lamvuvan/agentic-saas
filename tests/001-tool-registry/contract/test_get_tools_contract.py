"""T019: Contract test — GET /tools response matches get-tools.json schema."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


CONTRACT = json.loads((Path(__file__).parents[3] / "specs/001-tool-registry/contracts/get-tools.json").read_text())


@pytest.fixture
def client(tmp_path):
    import yaml

    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(yaml.dump({
        "tools": [
            {
                "name": "customer__get_customers",
                "namespace": "customer",
                "description": "Find customers by name or phone number.",
                "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
                "api": {"method": "GET", "url": "https://api.example.com/customers"},
            },
            {
                "name": "bi__run_query",
                "namespace": "bi",
                "description": "Run a SQL query.",
                "parameters": {"type": "object", "required": ["sql"], "properties": {"sql": {"type": "string"}}},
                "handler": "bi_query_handler",
            },
        ]
    }))

    from tool_registry.config_loader import ConfigLoader
    from tool_registry.main import create_app

    app = create_app()
    # Override config loader with temp file
    from tool_registry import main as main_module
    loader = ConfigLoader(yaml_path=str(tools_yaml))
    loader.load()

    with TestClient(app, raise_server_exceptions=True) as c:
        # Inject the loader directly
        app.state.config_loader = loader
        # Patch the global store used by the router
        import tool_registry.routers.tools as tools_router
        tools_router._get_store = lambda: loader.store
        yield c


class TestGetToolsContract:
    def test_returns_json_array(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_each_item_has_type_function(self, client):
        resp = client.get("/tools")
        for item in resp.json():
            assert item.get("type") == "function", f"Expected type='function', got {item.get('type')}"

    def test_each_item_has_function_block(self, client):
        resp = client.get("/tools")
        for item in resp.json():
            fn = item.get("function")
            assert fn is not None
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_function_name_matches_namespace_verb_pattern(self, client):
        import re
        pattern = re.compile(r"^[a-z0-9]+__[a-z0-9_]+$")
        resp = client.get("/tools")
        for item in resp.json():
            name = item["function"]["name"]
            assert pattern.match(name), f"name '{name}' does not match namespace__verb"

    def test_parameters_type_is_object(self, client):
        resp = client.get("/tools")
        for item in resp.json():
            params = item["function"]["parameters"]
            assert params.get("type") == "object"

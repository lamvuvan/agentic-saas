"""T036: Contract test — GET /health response matches health.json schema."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from fastapi.testclient import TestClient

CONTRACT = json.loads(
    (Path(__file__).parents[3] / "specs/001-tool-registry/contracts/health.json").read_text()
)


@pytest.fixture
def healthy_client(tmp_path):
    """Test client with a valid tools.yaml — service should be healthy."""
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        yaml.dump(
            {
                "tools": [
                    {
                        "name": "customer__get_customers",
                        "namespace": "customer",
                        "description": "Find customers",
                        "parameters": {"type": "object", "properties": {}},
                        "api": {"method": "GET", "url": "https://api.example.com/customers"},
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

    with TestClient(app, raise_server_exceptions=True) as c:
        # Override after lifespan runs to ensure our loader is active
        app.state.config_loader = loader
        yield c


@pytest.fixture
def degraded_client():
    """Test client where config_loader has startup_error set (degraded state)."""
    from tool_registry.main import create_app

    app = create_app()

    # Build a mock loader that reports degraded state
    mock_loader = MagicMock()
    mock_loader.is_healthy = False
    mock_loader.startup_error = "YAML parse error: mapping values are not allowed here"
    mock_loader.store.tool_count = 0
    mock_loader.store.config_path = "/app/config/tools.yaml"
    mock_loader.store.loaded_at = None

    with TestClient(app, raise_server_exceptions=True) as c:
        # Override after lifespan runs
        app.state.config_loader = mock_loader
        yield c


class TestHealthContractHealthy:
    def test_200_status_code(self, healthy_client):
        resp = healthy_client.get("/health")
        assert resp.status_code == 200

    def test_200_has_required_fields(self, healthy_client):
        """Healthy response must have status, tool_count, config_path, last_reload."""
        resp = healthy_client.get("/health")
        data = resp.json()

        schema = CONTRACT["response"]["200"]["schema"]
        for field in schema["required"]:
            assert field in data, f"Missing required field: {field}"

    def test_200_status_is_healthy(self, healthy_client):
        resp = healthy_client.get("/health")
        assert resp.json()["status"] == "healthy"

    def test_200_tool_count_is_integer(self, healthy_client):
        resp = healthy_client.get("/health")
        assert isinstance(resp.json()["tool_count"], int)

    def test_200_tool_count_matches_loaded_tools(self, healthy_client):
        resp = healthy_client.get("/health")
        assert resp.json()["tool_count"] == 1  # Only 1 tool in fixture

    def test_200_last_reload_is_string(self, healthy_client):
        resp = healthy_client.get("/health")
        last_reload = resp.json()["last_reload"]
        assert isinstance(last_reload, str)
        assert len(last_reload) > 0


class TestHealthContractDegraded:
    def test_503_status_code(self, degraded_client):
        resp = degraded_client.get("/health")
        assert resp.status_code == 503

    def test_503_has_required_fields(self, degraded_client):
        """Degraded response must have status and error."""
        resp = degraded_client.get("/health")
        data = resp.json()

        schema = CONTRACT["response"]["503"]["schema"]
        for field in schema["required"]:
            assert field in data, f"Missing required field: {field}"

    def test_503_status_is_degraded(self, degraded_client):
        resp = degraded_client.get("/health")
        assert resp.json()["status"] == "degraded"

    def test_503_error_is_nonempty_string(self, degraded_client):
        resp = degraded_client.get("/health")
        error = resp.json()["error"]
        assert isinstance(error, str)
        assert len(error) > 0

"""T020: Unit tests for GET /tools — namespace filtering, format, empty results."""
import pytest


def _make_store_with_tools():
    from tool_registry.config_loader import build_store, validate_tools

    raw = [
        {
            "name": "customer__get_customers",
            "namespace": "customer",
            "description": "Find customers",
            "parameters": {"type": "object", "properties": {}},
            "api": {"method": "GET", "url": "https://example.com"},
        },
        {
            "name": "bi__run_query",
            "namespace": "bi",
            "description": "Run SQL",
            "parameters": {"type": "object", "properties": {}},
            "handler": "bi_query_handler",
        },
    ]
    tools, _ = validate_tools(raw)
    return build_store(tools)


class TestToolsRouter:
    @pytest.fixture(autouse=True)
    def patch_store(self, monkeypatch):
        store = _make_store_with_tools()
        import tool_registry.routers.tools as tools_router
        monkeypatch.setattr(tools_router, "_get_store", lambda: store)

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from tool_registry.main import create_app
        app = create_app()
        # Disable lifespan for unit tests
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_no_filter_returns_all_tools(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        data = resp.json()
        names = [item["function"]["name"] for item in data]
        assert "customer__get_customers" in names
        assert "bi__run_query" in names

    def test_namespace_filter_customer(self, client):
        resp = client.get("/tools?namespace=customer")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["function"]["name"] == "customer__get_customers"

    def test_namespace_filter_bi(self, client):
        resp = client.get("/tools?namespace=bi")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["function"]["name"] == "bi__run_query"

    def test_unknown_namespace_returns_empty_list(self, client):
        resp = client.get("/tools?namespace=unknown")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_is_valid_json_array(self, client):
        resp = client.get("/tools")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

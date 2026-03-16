"""T031: Integration tests for handler dispatch — bi_query_handler with asyncpg mock."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_bi_tool(tmp_path):
    """FastAPI test app with the bi__run_query handler tool."""
    tools_yaml = tmp_path / "tools.yaml"
    tools_yaml.write_text(
        yaml.dump(
            {
                "tools": [
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

    # Import handler to auto-register it
    import tool_registry.handlers.bi_query_handler  # noqa: F401

    app = create_app()
    loader = ConfigLoader(yaml_path=str(tools_yaml))
    loader.load()
    app.state.config_loader = loader

    import tool_registry.routers.tools as tools_router

    tools_router._get_store = lambda: loader.store
    return app


def _mock_pool(rows: list[dict]):
    """Build a mock asyncpg pool that returns the given rows."""
    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[MagicMock(**row, **{"items.return_value": row.items()}) for row in rows])

    # Make dict(row) work by having each row behave like a mapping
    fetch_results = []
    for row in rows:
        mock_row = MagicMock()
        mock_row.__iter__ = MagicMock(return_value=iter(row.items()))
        mock_row.keys = MagicMock(return_value=row.keys())
        fetch_results.append(row)

    mock_conn.fetch = AsyncMock(return_value=fetch_results)

    mock_conn_ctx = AsyncMock()
    mock_conn_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn_ctx)
    return mock_pool, mock_conn


class TestHandlerDispatchIntegration:
    def test_bi_run_query_executes_select(self, app_with_bi_tool):
        """bi__run_query executes a SELECT query and returns rows."""
        rows = [{"total": 1500000, "date": "2024-01-01"}]
        mock_pool, mock_conn = _mock_pool(rows)

        with patch("tool_registry.handlers.bi_query_handler._pool", mock_pool):
            with TestClient(app_with_bi_tool, raise_server_exceptions=True) as client:
                resp = client.post(
                    "/tools/bi__run_query/execute",
                    json={"sql": "SELECT SUM(total) FROM orders", "limit": 10},
                )

        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_name"] == "bi__run_query"
        assert "result" in data

    def test_non_select_sql_rejected_with_validation_error(self, app_with_bi_tool):
        """Non-SELECT SQL must be rejected with HANDLER_ERROR (PermissionError from handler)."""
        with TestClient(app_with_bi_tool, raise_server_exceptions=False) as client:
            resp = client.post(
                "/tools/bi__run_query/execute",
                json={"sql": "DELETE FROM orders"},
            )

        # Handler raises PermissionError → dispatch maps to HANDLER_ERROR
        assert resp.status_code in (422, 502)
        data = resp.json()
        assert data["error_code"] in ("HANDLER_ERROR", "VALIDATION_ERROR")

    def test_limit_injected_when_absent(self, app_with_bi_tool):
        """LIMIT clause is injected into query when not present."""
        rows = [{"id": 1}]
        mock_pool, mock_conn = _mock_pool(rows)

        with patch("tool_registry.handlers.bi_query_handler._pool", mock_pool):
            with TestClient(app_with_bi_tool, raise_server_exceptions=True) as client:
                client.post(
                    "/tools/bi__run_query/execute",
                    json={"sql": "SELECT id FROM products"},
                )

        # Verify the SQL passed to fetch contained LIMIT
        mock_conn.fetch.assert_awaited_once()
        called_sql = mock_conn.fetch.call_args[0][0]
        assert "LIMIT" in called_sql.upper()

    def test_missing_sql_returns_error(self, app_with_bi_tool):
        """Missing 'sql' parameter must produce an error response."""
        with TestClient(app_with_bi_tool, raise_server_exceptions=False) as client:
            resp = client.post("/tools/bi__run_query/execute", json={})

        assert resp.status_code in (422, 502)
        data = resp.json()
        assert data["error_code"] in ("HANDLER_ERROR", "VALIDATION_ERROR")
        assert data["error"] is not None

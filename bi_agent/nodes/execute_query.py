"""Execute query node — runs SQL via Tool Registry bi__run_query."""

from __future__ import annotations

import logging

from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError

logger = logging.getLogger(__name__)


async def execute_query(
    sql: str,
    tool_registry_url: str,
) -> tuple[list[dict], bool]:
    """
    Execute SQL query via Tool Registry bi__run_query tool.

    Returns:
        (rows, limit_applied)
        - rows: list of result dicts
        - limit_applied: True if automatic LIMIT was injected
    """
    client = ToolRegistryClient(base_url=tool_registry_url)
    try:
        result = await client.execute("bi__run_query", {"sql": sql})

        rows: list[dict] = []
        if isinstance(result, list):
            rows = result
        elif isinstance(result, dict):
            rows = result.get("rows", result.get("data", []))

        return rows, False

    except ToolRegistryError as exc:
        logger.error(
            "bi_query_execution_failed",
            extra={"error": str(exc), "sql_preview": sql[:100]},
        )
        raise

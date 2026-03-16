"""BI query handler — direct asyncpg PostgreSQL execution.

Registered into HANDLER_REGISTRY at import time.
Full implementation in Phase 5 (T028).
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_pool = None  # asyncpg connection pool, initialised on first use


async def _get_pool():
    """Return (or create) the asyncpg connection pool."""
    global _pool
    if _pool is None:
        import asyncpg

        database_url = os.getenv("DATABASE_URL", "")
        # asyncpg uses postgresql:// not postgresql+asyncpg://
        dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        _pool = await asyncpg.create_pool(dsn)
    return _pool


def _inject_limit(sql: str, limit: int) -> str:
    """Inject LIMIT clause if not already present."""
    if re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        return sql
    return f"{sql.rstrip().rstrip(';')} LIMIT {limit}"


async def bi_query_handler(params: dict, auth) -> dict:
    """Execute a SELECT-only SQL query on the analytics PostgreSQL database.

    Security:
    - Only SELECT statements are allowed.
    - LIMIT is injected automatically if absent (max 500).

    Args:
        params: dict with 'sql' (required) and 'limit' (optional, default 100).
        auth:   AuthContext — tenant_id forwarded for row-level security if needed.

    Returns:
        dict with 'rows' key containing list of result row dicts.
    """
    sql: str = params.get("sql", "").strip()
    limit: int = min(int(params.get("limit", 100)), 500)

    if not sql:
        raise ValueError("'sql' parameter is required")

    if not sql.upper().startswith("SELECT"):
        raise PermissionError("Only SELECT statements are allowed. Non-SELECT SQL rejected.")

    sql_with_limit = _inject_limit(sql, limit)

    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql_with_limit)

    return {"rows": [dict(row) for row in rows]}


# Auto-register into HANDLER_REGISTRY at import time
from tool_registry.handlers import register_handler  # noqa: E402

register_handler("bi_query_handler", bi_query_handler)
logger.debug("Registered handler: bi_query_handler")

"""Async MongoDB client (Motor) with TTL index management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None  # type: ignore[type-arg]
_db: AsyncIOMotorDatabase | None = None  # type: ignore[type-arg]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def init_mongodb() -> None:
    """Create the Motor client and ensure the TTL index on request_logs."""
    global _client, _db
    settings = get_settings()
    _client = AsyncIOMotorClient(settings.database.mongodb_url)
    _db = _client[settings.database.mongodb_database]

    # TTL index: auto-delete documents after N days
    retention_seconds = settings.database.mongo_log_retention_days * 86400
    await _db.request_logs.create_index(
        "created_at",
        expireAfterSeconds=retention_seconds,
        name="ttl_created_at",
        background=True,
    )


async def close_mongodb() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None


def get_db() -> AsyncIOMotorDatabase:  # type: ignore[type-arg]
    if _db is None:
        raise RuntimeError("MongoDB not initialised — call init_mongodb() first")
    return _db


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

async def log_request(
    *,
    merchant_id: str | None,
    session_id: str | None,
    request_body: dict[str, Any],
    path: str,
    method: str,
) -> None:
    """Insert a request log document (fire-and-forget safe)."""
    try:
        db = get_db()
        await db.request_logs.insert_one({
            "type": "request",
            "merchant_id": merchant_id,
            "session_id": session_id,
            "path": path,
            "method": method,
            "body": request_body,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:
        logger.warning("Failed to log request to MongoDB: %s", exc)


async def log_response(
    *,
    merchant_id: str | None,
    session_id: str | None,
    response_body: dict[str, Any],
    status_code: int,
    latency_ms: float,
    model_used: str | None = None,
) -> None:
    """Insert a response log document (fire-and-forget safe)."""
    try:
        db = get_db()
        await db.request_logs.insert_one({
            "type": "response",
            "merchant_id": merchant_id,
            "session_id": session_id,
            "status_code": status_code,
            "body": response_body,
            "latency_ms": latency_ms,
            "model_used": model_used,
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as exc:
        logger.warning("Failed to log response to MongoDB: %s", exc)

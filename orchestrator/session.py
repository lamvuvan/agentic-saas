"""Session management — UserSession Redis store with conversation history."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_SESSION_TTL = 1800  # 30 minutes


def _session_key(session_id: str) -> str:
    return f"session:{session_id}"


async def get_or_create_session(
    redis: aioredis.Redis,
    session_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Get an existing session or create a new one.

    Returns:
        (session_id, session_data)
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    raw = await redis.get(_session_key(session_id))
    if raw:
        session = json.loads(raw)
        # Reset TTL on access
        await redis.expire(_session_key(session_id), _SESSION_TTL)
        return session_id, session

    session: dict[str, Any] = {
        "session_id": session_id,
        "tenant_id": "default",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_active_at": datetime.now(timezone.utc).isoformat(),
        "turns": [],
        "active_task_id": None,
    }
    await redis.set(_session_key(session_id), json.dumps(session), ex=_SESSION_TTL)
    return session_id, session


async def append_turn(
    redis: aioredis.Redis,
    session_id: str,
    role: str,
    content: str,
    intent: str | None = None,
    trace_id: str = "",
) -> None:
    """Append a conversation turn to the session history (max 20 turns stored)."""
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        _, session = await get_or_create_session(redis, session_id)
    else:
        session = json.loads(raw)

    turn = {
        "turn_id": str(uuid.uuid4()),
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "trace_id": trace_id,
    }
    session["turns"] = session.get("turns", [])[-19:] + [turn]  # Keep max 20
    session["last_active_at"] = datetime.now(timezone.utc).isoformat()

    await redis.set(_session_key(session_id), json.dumps(session), ex=_SESSION_TTL)


async def get_last_n_turns(
    redis: aioredis.Redis,
    session_id: str,
    n: int = 3,
) -> list[dict[str, str]]:
    """
    Retrieve the last N turns from session history for LLM context injection.

    Returns list of {role, content} dicts.
    """
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return []

    session = json.loads(raw)
    turns = session.get("turns", [])
    recent = turns[-n:] if len(turns) > n else turns
    return [{"role": t["role"], "content": t["content"]} for t in recent]


async def set_active_task(
    redis: aioredis.Redis,
    session_id: str,
    task_id: str | None,
) -> None:
    """Update the active A2A task ID for the session."""
    raw = await redis.get(_session_key(session_id))
    if raw is None:
        return
    session = json.loads(raw)
    session["active_task_id"] = task_id
    await redis.set(_session_key(session_id), json.dumps(session), ex=_SESSION_TTL)

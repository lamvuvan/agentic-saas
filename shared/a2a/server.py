"""Redis-backed A2A task store helpers — used by Domain Agent A2A servers."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from shared.a2a.models import A2ATask, TaskStatus

logger = logging.getLogger(__name__)

# Redis key pattern: a2a:task:{task_id}
_KEY_PREFIX = "a2a:task:"

# TTL (seconds) applied when task reaches a terminal state
TERMINAL_TTL = 3600  # 1 hour


def _task_key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


async def store_task(redis: aioredis.Redis, task: A2ATask) -> None:
    """Persist a new task. No TTL set until task reaches terminal state."""
    await redis.set(_task_key(task.task_id), task.model_dump_json())


async def get_task(redis: aioredis.Redis, task_id: str) -> A2ATask | None:
    """Fetch task by ID. Returns None if not found or expired."""
    raw = await redis.get(_task_key(task_id))
    if raw is None:
        return None
    return A2ATask.model_validate_json(raw)


async def update_task_status(
    redis: aioredis.Redis,
    task_id: str,
    status: TaskStatus,
    **kwargs: Any,
) -> A2ATask | None:
    """
    Atomically update task status + any extra fields.
    Applies 1-hour TTL when status is terminal (completed, failed, timeout).
    Returns the updated task, or None if task not found.
    """
    task = await get_task(redis, task_id)
    if task is None:
        logger.warning("update_task_status: task not found", extra={"task_id": task_id})
        return None

    updated = task.with_status(status, **kwargs)
    serialized = updated.model_dump_json()

    terminal = status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT)
    if terminal:
        await redis.set(_task_key(task_id), serialized, ex=TERMINAL_TTL)
    else:
        await redis.set(_task_key(task_id), serialized)

    return updated

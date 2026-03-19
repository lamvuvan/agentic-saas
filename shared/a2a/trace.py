"""Redis-backed step trace helpers — used by Domain Agent graph node wrappers.

Each completed/failed node execution is appended to a Redis list:
    Key:  step_trace:{task_id}
    TTL:  1 hour (refreshed on every write)
    Item: JSON-encoded StepEntry

The list grows as nodes execute; consumers call get_steps() to read all entries.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis

_TRACE_PREFIX = "step_trace:"
_TRACE_TTL = 3600  # 1 hour


def _trace_key(task_id: str) -> str:
    return f"{_TRACE_PREFIX}{task_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def append_step(
    redis: aioredis.Redis,
    task_id: str,
    node: str,
    status: str,
    started_at: str,
    completed_at: str | None = None,
    duration_ms: int | None = None,
    output_summary: dict[str, Any] | None = None,
) -> None:
    """Append one step entry to the trace list for task_id.

    Args:
        redis:          Redis connection
        task_id:        A2A task ID (used as key suffix)
        node:           LangGraph node name (e.g. "extract_entities")
        status:         "completed" | "failed"
        started_at:     ISO timestamp when node started
        completed_at:   ISO timestamp when node finished
        duration_ms:    Wall-clock duration in milliseconds
        output_summary: Lightweight per-node summary dict (never full state)
    """
    entry: dict[str, Any] = {
        "node": node,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "output_summary": output_summary or {},
    }
    key = _trace_key(task_id)
    await redis.rpush(key, json.dumps(entry, ensure_ascii=False))
    await redis.expire(key, _TRACE_TTL)


async def get_steps(redis: aioredis.Redis, task_id: str) -> list[dict[str, Any]]:
    """Return all trace steps for task_id in execution order."""
    raw_list = await redis.lrange(_trace_key(task_id), 0, -1)
    steps: list[dict[str, Any]] = []
    for raw in raw_list:
        try:
            steps.append(json.loads(raw))
        except Exception:
            pass
    return steps


def make_traced_node(
    node_name: str,
    fn: Any,
    extract_summary: Any,
) -> Any:
    """Return an async node wrapper that appends a trace step on completion.

    Args:
        node_name:       LangGraph node name string
        fn:              Original async node coroutine function (state, config) → dict
        extract_summary: Callable(node_name, result_dict) → lightweight summary dict

    The wrapper reads ``config["configurable"]["redis"]`` and
    ``state["task_id"]`` to know where to write the trace.
    If either is absent, the node runs un-traced (graceful degradation).
    """
    import functools  # stdlib — safe to import here

    @functools.wraps(fn)
    async def wrapper(state: dict, config: dict) -> dict:
        redis: aioredis.Redis | None = config.get("configurable", {}).get("redis")
        task_id: str = state.get("task_id", "")
        started_at = _now_iso()
        t0 = time.monotonic()

        try:
            result = await fn(state, config)
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if redis and task_id:
                try:
                    await append_step(
                        redis,
                        task_id,
                        node_name,
                        "failed",
                        started_at,
                        _now_iso(),
                        duration_ms,
                        {"error": str(exc)[:200]},
                    )
                except Exception:
                    pass
            raise

        duration_ms = int((time.monotonic() - t0) * 1000)
        if redis and task_id:
            try:
                summary = extract_summary(node_name, result or {})
                await append_step(
                    redis,
                    task_id,
                    node_name,
                    "completed",
                    started_at,
                    _now_iso(),
                    duration_ms,
                    summary,
                )
            except Exception:
                pass

        return result

    return wrapper

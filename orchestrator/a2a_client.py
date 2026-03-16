"""Orchestrator A2A client — submits tasks to Domain Agents and polls for results."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from shared.a2a.client import A2ATimeoutError, poll_task, submit_task
from shared.a2a.models import A2ATask, TaskStatus
from shared.auth_context import get_token

logger = logging.getLogger(__name__)

_POLL_INTERVAL_MS = int(os.environ.get("A2A_POLL_INTERVAL_MS", "500"))
_TIMEOUT_MS = int(os.environ.get("A2A_TIMEOUT_MS", "30000"))


async def submit_to_agent(
    agent_url: str,
    skill: str,
    params: dict[str, Any],
    trace_id: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """
    Submit a task to a Domain Agent and return the task_id.
    Forwards the caller's Bearer token via ContextVar.
    """
    token = get_token()
    task_id = await submit_task(
        agent_url=agent_url,
        skill=skill,
        params=params,
        token=token,
        trace_id=trace_id,
        http_client=http_client,
    )
    logger.info(
        "a2a_task_submitted",
        extra={"agent_url": agent_url, "skill": skill, "task_id": task_id},
    )
    return task_id


async def poll_for_result(
    agent_url: str,
    task_id: str,
    http_client: httpx.AsyncClient | None = None,
) -> A2ATask:
    """
    Poll for a task result until terminal state or 30s timeout.
    On timeout, returns a synthetic timeout task (not raises) so the
    Orchestrator graph can handle it gracefully.
    """
    token = get_token()
    try:
        task = await poll_task(
            agent_url=agent_url,
            task_id=task_id,
            token=token,
            poll_interval_ms=_POLL_INTERVAL_MS,
            timeout_ms=_TIMEOUT_MS,
            http_client=http_client,
        )
        logger.info(
            "a2a_task_result",
            extra={"agent_url": agent_url, "task_id": task_id, "status": task.status},
        )
        return task

    except A2ATimeoutError:
        logger.warning(
            "a2a_task_timeout",
            extra={"agent_url": agent_url, "task_id": task_id, "timeout_ms": _TIMEOUT_MS},
        )
        from shared.a2a.models import A2ATask as _A2ATask  # noqa: PLC0415

        return _A2ATask(
            task_id=task_id,
            skill="unknown",
            params={},
            status=TaskStatus.TIMEOUT,
            error=f"Agent at {agent_url} did not respond within {_TIMEOUT_MS}ms",
        )

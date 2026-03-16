"""A2A task submit + poll client — used by the Orchestrator."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from shared.a2a.models import A2ATask, TaskStatus

logger = logging.getLogger(__name__)


class A2ATimeoutError(Exception):
    """Raised when polling exceeds the configured timeout."""

    def __init__(self, agent_url: str, task_id: str, timeout_ms: int) -> None:
        super().__init__(
            f"A2A task {task_id} on {agent_url} timed out after {timeout_ms}ms"
        )
        self.task_id = task_id
        self.agent_url = agent_url


async def submit_task(
    agent_url: str,
    skill: str,
    params: dict[str, Any],
    token: str = "",
    trace_id: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """
    Submit an A2A task to a Domain Agent.
    Returns the task_id immediately (< 200ms).
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if trace_id:
        headers["X-Trace-Id"] = trace_id

    payload = {"skill": skill, "params": params}
    client = http_client or httpx.AsyncClient()
    try:
        resp = await client.post(
            f"{agent_url}/a2a/tasks",
            json=payload,
            headers=headers,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["task_id"]
    finally:
        if http_client is None:
            await client.aclose()


async def poll_task(
    agent_url: str,
    task_id: str,
    token: str = "",
    poll_interval_ms: int = 500,
    timeout_ms: int = 30000,
    http_client: httpx.AsyncClient | None = None,
) -> A2ATask:
    """
    Poll GET /a2a/tasks/{task_id} until terminal status.
    Raises A2ATimeoutError if timeout_ms is exceeded.

    Terminal states: completed, failed, timeout, input-required.
    """
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    elapsed_ms = 0
    poll_interval_s = poll_interval_ms / 1000.0
    client = http_client or httpx.AsyncClient()

    try:
        while elapsed_ms < timeout_ms:
            resp = await client.get(
                f"{agent_url}/a2a/tasks/{task_id}",
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            task = A2ATask.model_validate(resp.json())

            if task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.TIMEOUT,
                TaskStatus.INPUT_REQUIRED,
            ):
                return task

            await asyncio.sleep(poll_interval_s)
            elapsed_ms += poll_interval_ms
    finally:
        if http_client is None:
            await client.aclose()

    raise A2ATimeoutError(agent_url, task_id, timeout_ms)

"""BI Agent A2A server — POST /a2a/tasks, GET /a2a/tasks/{task_id}."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.a2a.models import A2AResult, A2ATask, TaskStatus
from shared.a2a.server import get_task, store_task, update_task_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a"])


class TaskSubmitRequest(BaseModel):
    skill: str
    params: dict[str, Any]


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str


async def _process_bi_task(task_id: str, task: A2ATask, redis: aioredis.Redis) -> None:
    """Execute the BI Agent graph for this task in the background."""
    await update_task_status(redis, task_id, TaskStatus.WORKING)

    try:
        # Graph invocation wired in T060 (Phase 6)
        from bi_agent.graph import run_bi_graph  # noqa: PLC0415

        result = await run_bi_graph(task_id=task_id, params=task.params)

        a2a_result = A2AResult(
            output=result.get("output", {}),
            reasoning_summary=result.get("reasoning_summary", "BI query processed"),
            confidence=result.get("confidence"),
            tool_calls=result.get("tool_calls", []),
        )
        await update_task_status(
            redis, task_id, TaskStatus.COMPLETED, result=a2a_result
        )

    except Exception as exc:
        logger.error(
            "bi_task_failed",
            extra={"task_id": task_id, "error": str(exc)},
            exc_info=True,
        )
        await update_task_status(
            redis, task_id, TaskStatus.FAILED, error=f"BI query failed: {exc}"
        )


@router.post("/tasks", response_model=TaskSubmitResponse, status_code=202)
async def submit_task(body: TaskSubmitRequest, request: Request) -> TaskSubmitResponse:
    redis: aioredis.Redis = request.app.state.redis
    task = A2ATask(skill=body.skill, params=body.params)
    await store_task(redis, task)

    asyncio.create_task(
        _process_bi_task(task.task_id, task, redis),
        name=f"bi_task_{task.task_id}",
    )

    return TaskSubmitResponse(task_id=task.task_id, status=task.status.value)


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str, request: Request) -> dict[str, Any]:
    redis: aioredis.Redis = request.app.state.redis
    task = await get_task(redis, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or expired")

    response: dict[str, Any] = {
        "task_id": task.task_id,
        "status": task.status.value,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }
    if task.result is not None:
        response["result"] = task.result.model_dump()
    if task.error:
        response["error"] = task.error
    return response

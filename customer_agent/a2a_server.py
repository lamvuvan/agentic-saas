"""Customer Agent A2A server — POST /a2a/tasks, GET /a2a/tasks/{task_id} — T134."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ValidationError

from shared.a2a.models import A2AResult, A2ATask, A2ATaskPayload, TaskStatus
from shared.a2a.server import get_task, store_task, update_task_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TaskSubmitRequest(BaseModel):
    skill: str
    params: dict[str, Any]


class TaskSubmitResponse(BaseModel):
    task_id: str
    status: str


# ---------------------------------------------------------------------------
# Background processor
# ---------------------------------------------------------------------------


async def _process_customer_task(
    task_id: str,
    task: A2ATask,
    redis: aioredis.Redis,
    memory: Any = None,
) -> None:
    """Execute the Customer Agent ReAct loop for this task in the background.

    Continuation resume (HITL):
        When params contains ``continuation.task_id`` (the interrupted task),
        check for pending HITL state and route to ``resume_after_hitl()``.
    """
    await update_task_status(redis, task_id, TaskStatus.WORKING)

    try:
        from customer_agent.core.react_loop import MemoryAwareReActLoop  # noqa: PLC0415

        params = task.params
        continuation = params.get("continuation")

        # ── Unpack A2ATaskPayload (if sent by DispatchEngine) ─────────────
        payload: A2ATaskPayload | None = None
        try:
            payload = A2ATaskPayload.model_validate(task.params)
        except (ValidationError, Exception):
            payload = None  # Backward compat: old-format plain dict params

        skill = task.skill or "lookup_customer"
        loop = MemoryAwareReActLoop(skill=skill)

        # ── HITL continuation path ────────────────────────────────────────
        if continuation and continuation.get("task_id"):
            original_task_id = continuation["task_id"]
            hitl_raw = await redis.get(f"hitl:{original_task_id}")
            if hitl_raw:
                user_input = continuation.get("user_input", "")
                tool_registry_url = os.environ.get("TOOL_REGISTRY_URL", "")
                result = await loop.resume_after_hitl(
                    user_response=user_input,
                    task_id=original_task_id,
                    redis=redis,
                    tool_registry_url=tool_registry_url,
                )

                if result.get("__scope_change__"):
                    a2a_result = A2AResult(
                        output={"__scope_change__": True, "new_request": result.get("new_request", "")},
                        reasoning_summary="Người dùng thay đổi yêu cầu sang chủ đề mới",
                        confidence=1.0,
                    )
                    await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                elif result.get("status") == "modify":
                    modified_params = {**params, "message": result.get("user_feedback", user_input)}
                    result2 = await loop.run(
                        task_id=task_id, params=modified_params, redis=redis, memory=memory
                    )
                    if result2.get("__hitl__"):
                        await update_task_status(
                            redis, task_id, TaskStatus.INPUT_REQUIRED,
                            input_request=result2.get("question", "Xác nhận thao tác?"),
                        )
                    else:
                        a2a_result = A2AResult(
                            output=result2.get("output", {}),
                            reasoning_summary="Customer task re-processed after HITL modify",
                            confidence=None,
                        )
                        await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                else:
                    outcome = result.get("status", "cancelled")
                    a2a_result = A2AResult(
                        output=result,
                        reasoning_summary=result.get("message", f"HITL {outcome}"),
                        confidence=1.0,
                    )
                    await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                return
        # ── End HITL continuation path ───────────────────────────────────

        result = await loop.run(
            task_id=task_id,
            params=params,
            memory=memory,
            payload=payload,
            redis=redis,
        )

        if result.get("__hitl__"):
            # HITL gate triggered — pause task and ask user
            await update_task_status(
                redis,
                task_id,
                TaskStatus.INPUT_REQUIRED,
                input_request=result.get("question", "Xác nhận thao tác?"),
            )
        else:
            output = result.get("output", {})
            a2a_result = A2AResult(
                output=output,
                reasoning_summary=output.get("message", "Customer task completed") if isinstance(output, dict) else "Customer task completed",
                confidence=None,
            )
            await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)

    except Exception as exc:
        logger.error(
            "customer_task_failed",
            extra={"task_id": task_id, "error": str(exc)},
            exc_info=True,
        )
        await update_task_status(
            redis, task_id, TaskStatus.FAILED, error=f"Customer processing failed: {exc}"
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/tasks", response_model=TaskSubmitResponse, status_code=202)
async def submit_task(body: TaskSubmitRequest, request: Request) -> TaskSubmitResponse:
    """Accept A2A task and return task_id immediately (< 200ms)."""
    redis: aioredis.Redis = request.app.state.redis
    memory = getattr(request.app.state, "memory", None)
    task = A2ATask(skill=body.skill, params=body.params)
    await store_task(redis, task)

    asyncio.create_task(
        _process_customer_task(task.task_id, task, redis, memory=memory),
        name=f"customer_task_{task.task_id}",
    )

    return TaskSubmitResponse(task_id=task.task_id, status=task.status.value)


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str, request: Request) -> dict[str, Any]:
    """Poll task status and result."""
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
    if task.input_request:
        response["input_request"] = task.input_request
    return response

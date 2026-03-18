"""Order Agent A2A server — POST /a2a/tasks, GET /a2a/tasks/{task_id}."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from shared.a2a.models import A2AResult, A2ATask, TaskStatus
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


async def _process_order_task(
    task_id: str,
    task: A2ATask,
    redis: aioredis.Redis,
    memory: Any = None,
) -> None:
    """
    Execute the Order Agent graph for this task in the background.

    Continuation resume:
        When params contains ``continuation.task_id`` (the original interrupted task),
        load the persisted OrderDraft from Redis and call run_order_graph with the
        continuation dict.  This mirrors ainvoke(None, {configurable: {thread_id: task_id}})
        in a full LangGraph StateGraph with AsyncRedisSaver checkpointing.
    """
    await update_task_status(redis, task_id, TaskStatus.WORKING)

    try:
        from order_agent.core.react_loop import MemoryAwareReActLoop  # noqa: PLC0415

        params = task.params
        continuation = params.get("continuation")

        # Resume path: detect continuation.task_id and load checkpoint
        if continuation and continuation.get("task_id"):
            original_task_id = continuation["task_id"]
            user_input = continuation.get("user_input", "")

            # Load draft checkpoint keyed by original task_id (thread_id=task_id)
            draft_json = await redis.get(f"order:draft:{original_task_id}")
            if draft_json is None:
                # Check AsyncRedisSaver checkpoint for task context
                try:
                    from order_agent.graph import _saver  # noqa: PLC0415

                    if _saver is not None:
                        config = {"configurable": {"thread_id": original_task_id}}
                        cp = await _saver.aget(config)
                        if cp:
                            draft_key = cp.get("channel_values", {}).get("draft_key")
                            if draft_key:
                                draft_json = await redis.get(draft_key)
                except Exception:  # pragma: no cover
                    pass

            if draft_json is None:
                await update_task_status(
                    redis,
                    task_id,
                    TaskStatus.FAILED,
                    error=f"No checkpoint found for original task {original_task_id}",
                )
                return

            # Inject the restored draft_json into params so run_order_graph uses it
            params = {
                **params,
                "continuation": {
                    "task_id": original_task_id,
                    "user_input": user_input,
                    "_draft_json": draft_json,  # pre-loaded for efficiency
                },
            }

        loop = MemoryAwareReActLoop(skill="create_order")

        # ── HITL continuation path ────────────────────────────────────────
        # If the original task had a pending HITL gate, route to resume_after_hitl()
        # instead of the normal order graph checkpoint resume.
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
                    # Signal scope change back to Orchestrator
                    a2a_result = A2AResult(
                        output={"__scope_change__": True, "new_request": result.get("new_request", "")},
                        reasoning_summary="Người dùng thay đổi yêu cầu sang chủ đề mới",
                        confidence=1.0,
                    )
                    await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                elif result.get("status") == "modify":
                    # Re-run graph with updated user feedback injected
                    modified_params = {**params, "message": result.get("user_feedback", user_input)}
                    result2 = await loop.run(task_id=task_id, params=modified_params, redis=redis, memory=memory)
                    if result2.get("status") == "input-required":
                        await update_task_status(
                            redis, task_id, TaskStatus.INPUT_REQUIRED,
                            input_request=result2.get("input_request", ""),
                        )
                    else:
                        a2a_result = A2AResult(
                            output=result2.get("output", {}),
                            reasoning_summary=result2.get("reasoning_summary", "Order re-processed after HITL modify"),
                            confidence=result2.get("confidence"),
                            tool_calls=result2.get("tool_calls", []),
                        )
                        await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                else:
                    # confirm, cancel, or error — all terminal
                    outcome = result.get("status", "cancelled")
                    a2a_result = A2AResult(
                        output=result,
                        reasoning_summary=result.get("message", f"HITL {outcome}"),
                        confidence=1.0,
                    )
                    await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)
                return
        # ── End HITL continuation path ───────────────────────────────────

        result = await loop.run(task_id=task_id, params=params, redis=redis, memory=memory)

        if result.get("status") == "input-required":
            await update_task_status(
                redis,
                task_id,
                TaskStatus.INPUT_REQUIRED,
                input_request=result.get("input_request", ""),
            )
        elif result.get("__hitl__"):
            # HITL gate triggered by graph (e.g., via _execute_tool signal)
            await update_task_status(
                redis,
                task_id,
                TaskStatus.INPUT_REQUIRED,
                input_request=result.get("question", "Xác nhận thao tác?"),
            )
        else:
            a2a_result = A2AResult(
                output=result.get("output", {}),
                reasoning_summary=result.get("reasoning_summary", "Order processed"),
                confidence=result.get("confidence"),
                tool_calls=result.get("tool_calls", []),
            )
            await update_task_status(
                redis, task_id, TaskStatus.COMPLETED, result=a2a_result
            )

    except Exception as exc:
        logger.error(
            "order_task_failed",
            extra={"task_id": task_id, "error": str(exc)},
            exc_info=True,
        )
        await update_task_status(
            redis, task_id, TaskStatus.FAILED, error=f"Order processing failed: {exc}"
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

    # Kick off background processing — do not await
    asyncio.create_task(
        _process_order_task(task.task_id, task, redis, memory=memory),
        name=f"order_task_{task.task_id}",
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

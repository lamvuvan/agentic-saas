"""Customer Agent A2A server — POST /a2a/tasks, GET /a2a/tasks/{task_id} (T063).

HITL pattern uses LangGraph native interrupt_before=["hitl_confirm"]:
    1. POST /a2a/tasks → run compiled.ainvoke(initial_state, config)
       → if confirm_message set (graph halted at interrupt) → status=input-required
    2. POST /a2a/tasks/{id}/resume → compiled.ainvoke(Command(resume={...}), config)
       → graph resumes from hitl_confirm node → status=completed
"""

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


class ResumeRequest(BaseModel):
    user_response: str


# ---------------------------------------------------------------------------
# Background processor
# ---------------------------------------------------------------------------


async def _process_customer_task(
    task_id: str,
    task: A2ATask,
    redis: aioredis.Redis,
    memory: Any = None,
) -> None:
    """Execute Customer Agent StateGraph for this task in the background.

    Uses LangGraph compiled.ainvoke() with interrupt_before=["hitl_confirm"].
    When the graph halts at the interrupt, sets task status to INPUT_REQUIRED.
    """
    await update_task_status(redis, task_id, TaskStatus.WORKING)

    try:
        from customer_agent.graph import _compiled, _build_graph  # noqa: PLC0415

        graph = _compiled
        if graph is None:
            from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

            graph = _build_graph().compile(
                checkpointer=MemorySaver(),
                interrupt_before=["hitl_confirm"],
            )

        params = task.params
        tenant_id = params.get("tenant_id", "default")

        initial_state = {
            "task_id": task_id,
            "plan_id": params.get("plan_id", ""),
            "original_message": params.get("message", params.get("original_message", "")),
            "instructions": params.get("instructions", ""),
            "conversation_history": params.get("conversation_history", []),
            "dependency_results": params.get("dependency_results", {}),
            "memory_context": "",
            "action": "",
            "customer_data": {},
            "confirm_message": "",
            "user_confirmation": "",
            "result": {},
        }

        config = {
            "configurable": {
                "thread_id": task_id,
                "memory": memory,
                "tenant_id": tenant_id,
                "tool_registry_url": os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001"),
            }
        }

        result_state = await graph.ainvoke(initial_state, config)

        # Detect interrupt: confirm_message set, user_confirmation empty
        if result_state.get("confirm_message") and not result_state.get("user_confirmation"):
            await update_task_status(
                redis,
                task_id,
                TaskStatus.INPUT_REQUIRED,
                input_request=result_state["confirm_message"],
            )
            return

        # Terminal state
        final_result = result_state.get("result", {})
        output = {k: v for k, v in final_result.items() if not k.startswith("__")}
        action = result_state.get("action", "lookup")

        a2a_result = A2AResult(
            output=output,
            reasoning_summary=final_result.get("message", f"Customer {action} completed."),
            tool_calls=[f"customer__{action}"] if final_result.get("status") == "success" else [],
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


async def _resume_customer_task(
    task_id: str,
    user_response: str,
    redis: aioredis.Redis,
    memory: Any = None,
    tenant_id: str = "default",
) -> None:
    """Resume an interrupted Customer Agent graph via Command(resume=...)."""
    await update_task_status(redis, task_id, TaskStatus.WORKING)

    try:
        from langgraph.types import Command  # noqa: PLC0415
        from customer_agent.graph import _compiled, _build_graph  # noqa: PLC0415

        graph = _compiled
        if graph is None:
            from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

            graph = _build_graph().compile(
                checkpointer=MemorySaver(),
                interrupt_before=["hitl_confirm"],
            )

        config = {
            "configurable": {
                "thread_id": task_id,
                "memory": memory,
                "tenant_id": tenant_id,
                "tool_registry_url": os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001"),
            }
        }

        result_state = await graph.ainvoke(
            Command(resume={"user_confirmation": user_response}),
            config=config,
        )

        final_result = result_state.get("result", {})
        output = {k: v for k, v in final_result.items() if not k.startswith("__")}
        action = result_state.get("action", "lookup")

        # Handle scope_change
        if final_result.get("__scope_change__"):
            a2a_result = A2AResult(
                output={"__scope_change__": True, "new_request": final_result.get("new_request", "")},
                reasoning_summary="Người dùng thay đổi yêu cầu sang chủ đề mới",
                confidence=1.0,
            )
        else:
            a2a_result = A2AResult(
                output=output,
                reasoning_summary=final_result.get("message", f"Customer {action} completed after confirmation."),
                tool_calls=[f"customer__{action}"] if final_result.get("status") == "success" else [],
            )

        await update_task_status(redis, task_id, TaskStatus.COMPLETED, result=a2a_result)

    except Exception as exc:
        logger.error(
            "customer_resume_failed",
            extra={"task_id": task_id, "error": str(exc)},
            exc_info=True,
        )
        await update_task_status(
            redis, task_id, TaskStatus.FAILED, error=f"Customer resume failed: {exc}"
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


@router.post("/tasks/{task_id}/resume", status_code=202)
async def resume_task(task_id: str, body: ResumeRequest, request: Request) -> dict[str, Any]:
    """Resume an interrupted task after user provides confirmation.

    Uses LangGraph Command(resume={"user_confirmation": body.user_response})
    to resume from the hitl_confirm node checkpoint.
    """
    redis: aioredis.Redis = request.app.state.redis
    memory = getattr(request.app.state, "memory", None)

    task = await get_task(redis, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or expired")
    if task.status != TaskStatus.INPUT_REQUIRED:
        raise HTTPException(
            status_code=409,
            detail=f"Task {task_id} is not awaiting input (status={task.status.value})",
        )

    tenant_id = task.params.get("tenant_id", "default") if task.params else "default"

    asyncio.create_task(
        _resume_customer_task(task_id, body.user_response, redis, memory=memory, tenant_id=tenant_id),
        name=f"customer_resume_{task_id}",
    )

    return {"task_id": task_id, "status": "working"}

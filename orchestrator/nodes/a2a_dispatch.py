"""A2A dispatch node — submits plan steps to Domain Agents and collects results."""

from __future__ import annotations

import logging
import os
from typing import Any

from shared.a2a.models import ExecutionPlan, PlanStep, TaskStatus

logger = logging.getLogger(__name__)

_AGENT_URLS: dict[str, str] = {
    "order-agent": os.environ.get("ORDER_AGENT_URL", "http://order-agent:8002"),
    "bi-agent": os.environ.get("BI_AGENT_URL", "http://bi-agent:8003"),
}


async def dispatch_plan(
    plan: ExecutionPlan,
    trace_id: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """
    Execute plan steps sequentially (MVP: single step only).

    Returns:
        (agent_results, needs_replan)
        - agent_results: list of result dicts from completed steps
        - needs_replan: True if a step failed and replanning should be triggered
    """
    from orchestrator.a2a_client import poll_for_result, submit_to_agent  # noqa: PLC0415

    agent_results: list[dict[str, Any]] = []
    needs_replan = False

    for step in plan.steps:
        if step.status != "pending":
            continue

        agent_url = _AGENT_URLS.get(step.agent)
        if agent_url is None:
            logger.error(
                "unknown_agent",
                extra={"agent": step.agent, "plan_id": plan.plan_id},
            )
            needs_replan = True
            break

        logger.info(
            "dispatching_step",
            extra={
                "step_id": step.step_id,
                "agent": step.agent,
                "skill": step.skill,
                "trace_id": trace_id,
            },
        )

        try:
            # Forward trace_id in params so domain agent traces link to same Langfuse trace
            params_with_trace = {**step.params, "_trace_id": trace_id}
            task_id = await submit_to_agent(
                agent_url=agent_url,
                skill=step.skill,
                params=params_with_trace,
                trace_id=trace_id,
            )
            step.task_id = task_id
            step.status = "running"

            task = await poll_for_result(agent_url=agent_url, task_id=task_id)

            if task.status == TaskStatus.TIMEOUT:
                logger.warning(
                    "step_timeout",
                    extra={"step_id": step.step_id, "task_id": task_id},
                )
                step.status = "failed"
                needs_replan = True
                break

            if task.status == TaskStatus.FAILED:
                step.status = "failed"
                needs_replan = True
                break

            if task.status == TaskStatus.INPUT_REQUIRED:
                step.status = "running"
                agent_results.append({
                    "step_id": step.step_id,
                    "status": "input-required",
                    "input_request": task.input_request or "",
                    "task_id": task_id,
                    "agent": step.agent,
                })
                break

            # Completed
            step.status = "completed"
            step.result = task.result.model_dump() if task.result else {}
            agent_results.append({
                "step_id": step.step_id,
                "status": "completed",
                "result": step.result,
                "agent": step.agent,
            })

        except Exception as exc:
            logger.error(
                "dispatch_error",
                extra={"step_id": step.step_id, "error": str(exc), "trace_id": trace_id},
                exc_info=True,
            )
            step.status = "failed"
            needs_replan = True
            break

    return agent_results, needs_replan

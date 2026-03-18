"""A2A dispatch node — submits plan steps to Domain Agents and collects results."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from shared.a2a.models import ExecutionPlan, TaskStatus

if TYPE_CHECKING:
    from orchestrator.core.plan_service import PlanService

logger = logging.getLogger(__name__)

# Fallback env-var URLs used when AgentRegistry is unavailable or unhealthy
_FALLBACK_AGENT_URLS: dict[str, str] = {
    "order-agent": os.environ.get("ORDER_AGENT_URL", "http://order-agent:8002"),
    "bi-agent": os.environ.get("BI_AGENT_URL", "http://bi-agent:8003"),
}


def _resolve_agent_url(agent_name: str, registry: Any) -> str | None:
    """Resolve agent URL: prefer AgentRegistry, fall back to env vars."""
    if registry is not None:
        url = registry.get_a2a_endpoint(agent_name)
        if url:
            return url
    return _FALLBACK_AGENT_URLS.get(agent_name)


def _extract_result_summary(task: Any) -> str | None:
    """Best-effort: extract a short result summary from A2ATask for plan_sub_goals.result_summary."""
    if task.result is None:
        return None
    try:
        output = task.result.output
        if isinstance(output, str):
            return output[:200]
        if isinstance(output, dict):
            # Try common keys
            for key in ("message", "summary", "formatted_summary", "reply"):
                if key in output:
                    return str(output[key])[:200]
            return str(output)[:200]
    except Exception:
        pass
    return None


async def dispatch_plan(
    plan: ExecutionPlan,
    trace_id: str = "",
    registry: Any = None,
    plan_service: "PlanService | None" = None,
    plan_id: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    Execute plan steps sequentially (MVP: single step only).

    When plan_service + plan_id are provided:
    - Calls plan_service.link_task() immediately after each A2A submit (sets sub_goal status=running)
    - Calls plan_service.sync_from_task() after polling completes (syncs final status + result_summary)

    Returns:
        (agent_results, needs_replan)
        - agent_results: list of result dicts from completed steps
        - needs_replan: True if a step failed and replanning should be triggered
    """
    from orchestrator.a2a_client import poll_for_result, submit_to_agent  # noqa: PLC0415

    agent_results: list[dict[str, Any]] = []
    needs_replan = False

    for sequence, step in enumerate(plan.steps, start=1):
        if step.status != "pending":
            continue

        agent_url = _resolve_agent_url(step.agent, registry)
        if agent_url is None:
            logger.error(
                "unknown_agent",
                extra={"agent": step.agent, "plan_id": plan.plan_id},
            )
            needs_replan = True
            # Sync sub_goal to failed if we have a plan_service
            if plan_service and plan_id:
                try:
                    await plan_service.sync_from_task("", "failed")
                except Exception:
                    pass
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

            # Link A2A task to sub_goal immediately after submit (status → running)
            if plan_service and plan_id:
                try:
                    await plan_service.link_task(plan_id, sequence, task_id)
                except Exception as exc:
                    logger.warning(
                        "plan_link_task_failed",
                        extra={"task_id": task_id, "error": str(exc)},
                    )

            task = await poll_for_result(agent_url=agent_url, task_id=task_id)

            # Sync final status + result_summary to sub_goal
            if plan_service and plan_id:
                try:
                    result_summary = _extract_result_summary(task)
                    await plan_service.sync_from_task(
                        task_id, task.status.value, result_summary
                    )
                except Exception as exc:
                    logger.warning(
                        "plan_sync_from_task_failed",
                        extra={"task_id": task_id, "error": str(exc)},
                    )

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

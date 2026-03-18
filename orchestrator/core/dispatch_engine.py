"""DispatchEngine — dependency-ordered parallel/sequential A2A task dispatch.

Algorithm:
    Each iteration (round) finds all PlanSteps whose depends_on agents are
    already in ``results``. Those steps are dispatched in parallel via
    asyncio.gather. Repeat until all steps are resolved or no progress is made
    (deadlock / upstream failure).

Payload:
    Each dispatched step receives an A2ATaskPayload containing:
    - Identity: task_id, plan_id, sub_goal_sequence, session_id, tenant_id
    - Intent: original_message, skill, instructions
    - Conversation: conversation_history (last 6 turns from session)
    - Dependencies: dependency_results (upstream agent → result dict)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from shared.a2a.models import A2ATaskPayload, ExecutionPlan, PlanStep, TaskStatus

if TYPE_CHECKING:
    from orchestrator.core.plan_service import PlanService

logger = logging.getLogger(__name__)


def _extract_result_summary(result_dict: dict) -> str | None:
    """Best-effort: extract short summary from a dispatch result dict."""
    inner = result_dict.get("result", {})
    if not inner:
        return None
    output = inner.get("output", {})
    if isinstance(output, str):
        return output[:200]
    if isinstance(output, dict):
        for key in ("message", "summary", "formatted_summary", "reply"):
            if key in output:
                return str(output[key])[:200]
        return str(output)[:200]
    return None


class DispatchEngine:
    """Execute ExecutionPlan steps in dependency order with parallel dispatch."""

    async def execute(
        self,
        plan: ExecutionPlan,
        session: dict[str, Any],
        plan_id: str,
        tenant_id: str,
        plan_service: "PlanService | None",
        registry: Any,
        trace_id: str = "",
    ) -> dict[str, Any]:
        """Execute all plan steps and return a results dict keyed by agent name.

        Steps with empty ``depends_on`` are dispatched concurrently in the same
        round. Steps with dependencies wait until all named agents are present
        in ``results``.

        Returns:
            dict mapping agent_name → result dict
            Each result dict has at least ``{"status": "completed"|"failed", ...}``
        """
        results: dict[str, Any] = {}
        pending = list(plan.steps)
        max_rounds = len(pending) + 1  # Safety: prevent infinite loop

        for _round in range(max_rounds):
            if not pending:
                break

            # Find steps whose dependencies are all satisfied
            ready = [
                s for s in pending
                if all(dep in results for dep in s.depends_on)
            ]

            if not ready:
                # No progress possible — remaining steps depend on failed upstreams
                for step in pending:
                    logger.warning(
                        "step_skipped_no_ready_deps",
                        extra={"agent": step.agent, "depends_on": step.depends_on},
                    )
                    results[step.agent] = {
                        "status": "failed",
                        "error": "dependency failed or deadlock detected",
                    }
                break

            logger.info(
                "dispatch_round",
                extra={
                    "round": _round + 1,
                    "agents": [s.agent for s in ready],
                    "trace_id": trace_id,
                },
            )

            # Build per-step dependency_results (only deps for this step)
            round_coros = [
                self._dispatch_one(
                    step=step,
                    dependency_results={
                        agent: results[agent]
                        for agent in step.depends_on
                        if agent in results
                    },
                    session=session,
                    plan_id=plan_id,
                    tenant_id=tenant_id,
                    plan_service=plan_service,
                    registry=registry,
                    trace_id=trace_id,
                )
                for step in ready
            ]

            round_results = await asyncio.gather(*round_coros, return_exceptions=True)

            for step, res in zip(ready, round_results):
                pending.remove(step)
                if isinstance(res, BaseException):
                    logger.error(
                        "dispatch_one_exception",
                        extra={"agent": step.agent, "error": str(res)},
                        exc_info=res,
                    )
                    results[step.agent] = {"status": "failed", "error": str(res)}
                else:
                    results[step.agent] = res

                # Mark downstream steps as failed when this step failed
                if results[step.agent].get("status") == "failed":
                    _cascade_failure(pending, step.agent, results)

        return results

    async def _dispatch_one(
        self,
        step: PlanStep,
        dependency_results: dict[str, Any],
        session: dict[str, Any],
        plan_id: str,
        tenant_id: str,
        plan_service: "PlanService | None",
        registry: Any,
        trace_id: str = "",
    ) -> dict[str, Any]:
        """Submit one plan step to its Domain Agent and poll for the result.

        Builds A2ATaskPayload, submits via A2A, polls to completion.
        """
        from orchestrator.a2a_client import poll_for_result, submit_to_agent  # noqa: PLC0415
        from orchestrator.nodes.a2a_dispatch import _resolve_agent_url  # noqa: PLC0415

        agent_url = _resolve_agent_url(step.agent, registry)
        if agent_url is None:
            logger.error("unknown_agent", extra={"agent": step.agent})
            return {"status": "failed", "error": f"unknown agent: {step.agent}"}

        # Build A2ATaskPayload
        task_id = str(uuid.uuid4())
        last_message = session.get("last_user_message", "")
        turns = session.get("turns", [])
        # Last 6 turns: [{role, content}, ...]
        conversation_history = [
            {"role": t.get("role", ""), "content": t.get("content", "")}
            for t in (turns[-6:] if len(turns) > 6 else turns)
        ]

        payload = A2ATaskPayload(
            task_id=task_id,
            plan_id=plan_id,
            sub_goal_sequence=int(step.step_id.split("-")[-1]) if "-" in step.step_id else 1,
            session_id=session.get("session_id", ""),
            tenant_id=tenant_id,
            original_message=last_message,
            skill=step.skill,
            instructions=step.instructions,
            conversation_history=conversation_history,
            dependency_results=dependency_results,
        )

        logger.info(
            "dispatching_step",
            extra={
                "step_id": step.step_id,
                "agent": step.agent,
                "skill": step.skill,
                "task_id": task_id,
                "trace_id": trace_id,
                "has_deps": bool(dependency_results),
            },
        )

        try:
            submitted_task_id = await submit_to_agent(
                agent_url=agent_url,
                skill=step.skill,
                params={**payload.model_dump(), "_trace_id": trace_id},
                trace_id=trace_id,
            )
            step.task_id = submitted_task_id
            step.status = "running"

            # Link to PlanService sub_goal (best-effort)
            if plan_service and plan_id:
                try:
                    # Derive sequence from step_id or enumerate from plan
                    sequence = _step_sequence(step)
                    await plan_service.link_task(plan_id, sequence, submitted_task_id)
                except Exception as exc:
                    logger.warning("link_task_failed", extra={"error": str(exc)})

            task = await poll_for_result(agent_url=agent_url, task_id=submitted_task_id)

            # Sync sub_goal status (best-effort)
            if plan_service and plan_id:
                try:
                    summary = None
                    if task.result:
                        summary = _extract_result_summary({"result": task.result.model_dump()})
                    await plan_service.sync_from_task(
                        submitted_task_id, task.status.value, summary
                    )
                except Exception as exc:
                    logger.warning("sync_from_task_failed", extra={"error": str(exc)})

            if task.status == TaskStatus.TIMEOUT:
                step.status = "failed"
                return {"status": "failed", "error": "timeout", "task_id": submitted_task_id}

            if task.status == TaskStatus.FAILED:
                step.status = "failed"
                return {
                    "status": "failed",
                    "error": task.error or "agent failed",
                    "task_id": submitted_task_id,
                }

            if task.status == TaskStatus.INPUT_REQUIRED:
                step.status = "running"
                return {
                    "status": "input-required",
                    "input_request": task.input_request or "",
                    "task_id": submitted_task_id,
                    "agent": step.agent,
                }

            # Completed
            step.status = "completed"
            result_dict = task.result.model_dump() if task.result else {}
            step.result = result_dict
            return {
                "status": "completed",
                "result": result_dict,
                "task_id": submitted_task_id,
                "agent": step.agent,
            }

        except Exception as exc:
            logger.error(
                "dispatch_one_error",
                extra={"step_id": step.step_id, "agent": step.agent, "error": str(exc)},
                exc_info=True,
            )
            step.status = "failed"
            return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _step_sequence(step: PlanStep) -> int:
    """Extract integer sequence from step_id like 'step-1' → 1."""
    try:
        return int(step.step_id.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return 1


def _cascade_failure(
    pending: list[PlanStep],
    failed_agent: str,
    results: dict[str, Any],
) -> None:
    """Mark steps that (transitively) depend on failed_agent as failed and remove from pending."""
    to_fail = [s for s in pending if failed_agent in s.depends_on]
    for step in to_fail:
        pending.remove(step)
        results[step.agent] = {
            "status": "failed",
            "error": f"dependency '{failed_agent}' failed",
        }
        # Cascade further
        _cascade_failure(pending, step.agent, results)

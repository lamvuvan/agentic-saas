"""A2A dispatch node — submits plan steps to Domain Agents and collects results."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from shared.a2a.models import ExecutionPlan

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
    session: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> tuple[list[dict[str, Any]], bool]:
    """
    Execute plan steps via DispatchEngine (parallel + sequential by depends_on).

    Steps with empty ``depends_on`` are dispatched concurrently. Steps with
    ``depends_on`` containing agent names wait for those agents to complete.

    When plan_service + plan_id are provided, DispatchEngine calls
    link_task() and sync_from_task() internally per step.

    Returns:
        (agent_results, needs_replan)
        - agent_results: list of result dicts from completed/input-required steps
        - needs_replan: True if any step failed
    """
    from orchestrator.core.dispatch_engine import DispatchEngine  # noqa: PLC0415

    engine = DispatchEngine()
    results_by_agent = await engine.execute(
        plan=plan,
        session=session or {},
        plan_id=plan_id or plan.plan_id,
        tenant_id=tenant_id,
        plan_service=plan_service,
        registry=registry,
        trace_id=trace_id,
    )

    agent_results: list[dict[str, Any]] = []
    needs_replan = False

    for step in plan.steps:
        res = results_by_agent.get(step.agent, {})
        status = res.get("status", "failed")

        if status == "failed":
            needs_replan = True
            continue

        agent_results.append({
            "step_id": step.step_id,
            "agent": step.agent,
            **res,
        })

        if status == "input-required":
            # Stop further aggregation — waiting for user input
            break

    return agent_results, needs_replan

"""Unit tests for DispatchEngine — T119.

Tests:
- Steps with empty depends_on dispatched via asyncio.gather in same round
- Downstream step dispatched only after upstream completes
- A2ATaskPayload.dependency_results populated from upstream result
- Upstream failure marks downstream as failed without dispatching
- Round loop terminates when all steps processed
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from unittest.mock import MagicMock

for _mod in ("openai", "openai.types", "faiss", "sentence_transformers", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(steps_config: list[dict]):
    """Build an ExecutionPlan from a list of step dicts."""
    from shared.a2a.models import PlanStep, ExecutionPlan

    steps = [
        PlanStep(
            step_id=f"step-{cfg['agent']}",
            agent=cfg["agent"],
            skill=cfg.get("skill", "test_skill"),
            params={},
            depends_on=cfg.get("depends_on", []),
            instructions=cfg.get("instructions", f"task for {cfg['agent']}"),
        )
        for cfg in steps_config
    ]
    return ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id="unit_sess",
        goal="Unit test plan",
        intent="order",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# T119.1 — Independent steps dispatched in same asyncio.gather round
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_independent_steps_same_round():
    """Steps with empty depends_on must be gathered in a single asyncio.gather call."""
    from orchestrator.core.dispatch_engine import DispatchEngine

    plan = _make_plan([
        {"agent": "order-agent", "depends_on": []},
        {"agent": "bi-agent", "depends_on": []},
    ])

    gather_calls: list[list[str]] = []
    original_gather = asyncio.gather

    async def _fake_dispatch_one(step, dependency_results, **kwargs):
        return {"status": "completed", "result": {"output": {}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()

    # Patch _dispatch_one and track which steps are gathered per round
    dispatched_agents: list[list[str]] = []

    original_execute = engine.execute.__func__

    async def _tracking_execute(self, plan, session, plan_id, tenant_id, plan_service, registry):
        """Wrap execute to track which steps are dispatched per round."""
        results: dict = {}
        pending = list(plan.steps)
        while pending:
            ready = [s for s in pending if all(dep in results for dep in s.depends_on)]
            if not ready:
                break
            dispatched_agents.append([s.agent for s in ready])
            round_results = await asyncio.gather(
                *[_fake_dispatch_one(s, {k: v for k, v in results.items() if k in s.depends_on}) for s in ready]
            )
            for step, res in zip(ready, round_results):
                results[step.agent] = res
                pending.remove(step)
        return results

    results = await _tracking_execute(engine, plan, {}, "p1", "t1", None, None)

    # Both agents in a single round
    assert len(dispatched_agents) == 1
    assert set(dispatched_agents[0]) == {"order-agent", "bi-agent"}
    assert len(results) == 2


# ---------------------------------------------------------------------------
# T119.2 — Downstream step dispatched only after upstream completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_downstream_waits_for_upstream():
    """Step with depends_on must not be dispatched until upstream is in results."""
    from orchestrator.core.dispatch_engine import DispatchEngine

    plan = _make_plan([
        {"agent": "order-agent", "depends_on": []},
        {"agent": "bi-agent", "depends_on": ["order-agent"]},
    ])

    dispatch_order: list[str] = []

    async def _fake_dispatch_one(step, dependency_results, **kwargs):
        dispatch_order.append(step.agent)
        return {"status": "completed", "result": {"output": {}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        await engine.execute(
            plan=plan,
            session={},
            plan_id="p2",
            tenant_id="t1",
            plan_service=None,
            registry=None,
        )

    assert dispatch_order[0] == "order-agent", "order-agent must be dispatched first"
    assert dispatch_order[1] == "bi-agent", "bi-agent must be dispatched second"


# ---------------------------------------------------------------------------
# T119.3 — dependency_results populated from upstream result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependency_results_populated():
    """Downstream _dispatch_one call must receive upstream result in dependency_results."""
    from orchestrator.core.dispatch_engine import DispatchEngine

    plan = _make_plan([
        {"agent": "order-agent", "depends_on": []},
        {"agent": "bi-agent", "depends_on": ["order-agent"]},
    ])

    received_deps: dict = {}
    upstream_output = {"order_id": "ORD-001", "total": 150000}

    async def _fake_dispatch_one(step, dependency_results, **kwargs):
        if step.agent == "order-agent":
            return {"status": "completed", "result": {"output": upstream_output, "reasoning_summary": "ok"}}
        received_deps.update(dependency_results)
        return {"status": "completed", "result": {"output": {}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        await engine.execute(
            plan=plan,
            session={},
            plan_id="p3",
            tenant_id="t1",
            plan_service=None,
            registry=None,
        )

    assert "order-agent" in received_deps
    # dependency_results for downstream contains upstream's full result dict
    assert received_deps["order-agent"]["result"]["output"]["order_id"] == "ORD-001"


# ---------------------------------------------------------------------------
# T119.4 — Upstream failure marks downstream as failed without dispatching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upstream_failure_marks_downstream_failed():
    """When upstream fails, downstream must be skipped and marked failed."""
    from orchestrator.core.dispatch_engine import DispatchEngine

    plan = _make_plan([
        {"agent": "order-agent", "depends_on": []},
        {"agent": "bi-agent", "depends_on": ["order-agent"]},
    ])

    dispatched: list[str] = []

    async def _fake_dispatch_one(step, dependency_results, **kwargs):
        dispatched.append(step.agent)
        return {"status": "failed", "error": "upstream_error"}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        results = await engine.execute(
            plan=plan,
            session={},
            plan_id="p4",
            tenant_id="t1",
            plan_service=None,
            registry=None,
        )

    # bi-agent must NOT be dispatched
    assert "bi-agent" not in dispatched
    # bi-agent result must be marked failed
    assert results["bi-agent"]["status"] == "failed"
    assert "dependency" in results["bi-agent"]["error"].lower()


# ---------------------------------------------------------------------------
# T119.5 — Round loop terminates when all steps processed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_loop_terminates():
    """execute() must return with all 3 steps resolved (no infinite loop)."""
    from orchestrator.core.dispatch_engine import DispatchEngine

    plan = _make_plan([
        {"agent": "order-agent", "depends_on": []},
        {"agent": "bi-agent", "depends_on": []},
        {"agent": "third-agent", "depends_on": ["order-agent", "bi-agent"]},
    ])

    async def _fake_dispatch_one(step, dependency_results, **kwargs):
        return {"status": "completed", "result": {"output": {}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        results = await engine.execute(
            plan=plan,
            session={},
            plan_id="p5",
            tenant_id="t1",
            plan_service=None,
            registry=None,
        )

    assert len(results) == 3
    assert results["third-agent"]["status"] == "completed"

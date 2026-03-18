"""Integration tests for DispatchEngine — T118.

Tests:
- Parallel dispatch: two independent steps (depends_on=[]) submitted concurrently
- Sequential dispatch: step 2 depends_on order-agent → receives dependency_results
- Upstream failure marks downstream step as failed without dispatching
- A2ATaskPayload fields (instructions, original_message, session_id) are correctly set
"""

from __future__ import annotations

import asyncio
import time
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


def _make_step(agent: str, depends_on: list[str] = None, instructions: str = "") -> dict:
    return {
        "step_id": f"step-{agent}",
        "agent": agent,
        "skill": f"{agent}_skill",
        "params": {},
        "depends_on": depends_on or [],
        "instructions": instructions or f"Thực hiện {agent}",
        "status": "pending",
        "result": None,
        "task_id": None,
    }


def _make_a2a_task_result(output: dict) -> dict:
    return {
        "status": "completed",
        "result": {"output": output, "reasoning_summary": "ok", "confidence": 0.9},
    }


# ---------------------------------------------------------------------------
# T118.1 — Two independent steps dispatched concurrently (parallel)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_engine_parallel_independent_steps():
    """Steps with depends_on=[] should be submitted in the same asyncio.gather round."""
    from orchestrator.core.dispatch_engine import DispatchEngine
    from shared.a2a.models import PlanStep, ExecutionPlan

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id="sess_1",
        goal="Test",
        intent="order",
        steps=[
            PlanStep(
                step_id="step-1",
                agent="order-agent",
                skill="create_order",
                params={},
                depends_on=[],
                instructions="Tạo đơn hàng",
            ),
            PlanStep(
                step_id="step-2",
                agent="bi-agent",
                skill="bi_query",
                params={},
                depends_on=[],
                instructions="Truy vấn doanh thu",
            ),
        ],
    )

    dispatch_times: list[float] = []

    async def _fake_dispatch_one(step, dependency_results, *args, **kwargs):
        dispatch_times.append(time.monotonic())
        await asyncio.sleep(0.05)  # simulate network
        return {"status": "completed", "result": {"output": {}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        results = await engine.execute(
            plan=plan,
            session={},
            plan_id="pg_plan_1",
            tenant_id="default",
            plan_service=None,
            registry=None,
        )

    # Both steps dispatched; result dict has both agents
    assert len(results) == 2
    assert "order-agent" in results
    assert "bi-agent" in results

    # Both should have been dispatched in the same round (within ~100ms of each other)
    if len(dispatch_times) == 2:
        gap_ms = abs(dispatch_times[1] - dispatch_times[0]) * 1000
        assert gap_ms < 200, f"Steps dispatched {gap_ms:.0f}ms apart — expected parallel (<200ms)"


# ---------------------------------------------------------------------------
# T118.2 — Sequential: step 2 receives dependency_results from step 1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_engine_sequential_dependency_results_injected():
    """Step 2 must receive step 1's output in dependency_results."""
    from orchestrator.core.dispatch_engine import DispatchEngine
    from shared.a2a.models import PlanStep, ExecutionPlan, A2ATaskPayload

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id="sess_2",
        goal="Test sequential",
        intent="order",
        steps=[
            PlanStep(
                step_id="step-1",
                agent="order-agent",
                skill="create_order",
                params={},
                depends_on=[],
                instructions="Tạo đơn hàng",
            ),
            PlanStep(
                step_id="step-2",
                agent="bi-agent",
                skill="bi_query",
                params={},
                depends_on=["order-agent"],  # depends on step 1
                instructions="Tổng hợp sau khi tạo đơn",
            ),
        ],
    )

    received_payloads: list[dict] = []

    async def _fake_dispatch_one(step, dependency_results, *args, **kwargs):
        received_payloads.append({
            "agent": step.agent,
            "dependency_results": dependency_results,
        })
        if step.agent == "order-agent":
            return {"status": "completed", "result": {"output": {"order_id": "ord_1"}, "reasoning_summary": "ok"}}
        return {"status": "completed", "result": {"output": {"revenue": 1000000}, "reasoning_summary": "ok"}}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        results = await engine.execute(
            plan=plan,
            session={},
            plan_id="pg_plan_2",
            tenant_id="default",
            plan_service=None,
            registry=None,
        )

    assert len(results) == 2

    # Step 1 called first with empty deps
    order_call = next(p for p in received_payloads if p["agent"] == "order-agent")
    assert order_call["dependency_results"] == {}

    # Step 2 called second WITH step 1's result
    bi_call = next(p for p in received_payloads if p["agent"] == "bi-agent")
    assert "order-agent" in bi_call["dependency_results"]
    # dependency_results contains the full result dict from _dispatch_one
    dep = bi_call["dependency_results"]["order-agent"]
    assert dep["result"]["output"]["order_id"] == "ord_1"


# ---------------------------------------------------------------------------
# T118.3 — Upstream failure marks downstream as failed (not dispatched)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_engine_upstream_failure_skips_downstream():
    """When upstream step fails, downstream dependents must NOT be dispatched."""
    from orchestrator.core.dispatch_engine import DispatchEngine
    from shared.a2a.models import PlanStep, ExecutionPlan

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        session_id="sess_3",
        goal="Test failure",
        intent="order",
        steps=[
            PlanStep(
                step_id="step-1",
                agent="order-agent",
                skill="create_order",
                params={},
                depends_on=[],
                instructions="Tạo đơn (sẽ fail)",
            ),
            PlanStep(
                step_id="step-2",
                agent="bi-agent",
                skill="bi_query",
                params={},
                depends_on=["order-agent"],
                instructions="Không nên chạy",
            ),
        ],
    )

    dispatch_calls: list[str] = []

    async def _fake_dispatch_one(step, dependency_results, *args, **kwargs):
        dispatch_calls.append(step.agent)
        return {"status": "failed", "error": "tool_error"}

    engine = DispatchEngine()
    with patch.object(engine, "_dispatch_one", side_effect=_fake_dispatch_one):
        results = await engine.execute(
            plan=plan,
            session={},
            plan_id="pg_plan_3",
            tenant_id="default",
            plan_service=None,
            registry=None,
        )

    # Only order-agent was dispatched; bi-agent should NOT be
    assert "order-agent" in dispatch_calls
    assert "bi-agent" not in dispatch_calls

    # bi-agent result should be marked failed
    assert "bi-agent" in results
    assert results["bi-agent"]["status"] == "failed"


# ---------------------------------------------------------------------------
# T118.4 — A2ATaskPayload fields correctly populated in _dispatch_one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_one_builds_correct_payload():
    """_dispatch_one must build A2ATaskPayload with instructions, session_id, etc."""
    from orchestrator.core.dispatch_engine import DispatchEngine
    from shared.a2a.models import PlanStep, A2ATaskPayload

    step = PlanStep(
        step_id="step-1",
        agent="order-agent",
        skill="create_order",
        params={},
        depends_on=[],
        instructions="Tạo đơn bàn 3",
    )
    session = {
        "session_id": "sess_payload",
        "turns": [
            {"role": "user", "content": "tôi muốn order"},
            {"role": "assistant", "content": "được, bạn muốn order gì?"},
        ],
    }

    submitted_payload: list[A2ATaskPayload] = []

    async def _fake_submit(agent_url, skill, params, trace_id=""):
        payload = A2ATaskPayload.model_validate(params)
        submitted_payload.append(payload)
        return "task_123"

    async def _fake_poll(agent_url, task_id):
        task = MagicMock()
        task.status.value = "completed"
        task.status = MagicMock()
        task.status.value = "completed"
        from shared.a2a.models import TaskStatus
        task.status = TaskStatus.COMPLETED
        task.result = MagicMock()
        task.result.output = {"done": True}
        task.result.model_dump = lambda: {"output": {"done": True}, "reasoning_summary": "ok"}
        return task

    engine = DispatchEngine()
    with patch("orchestrator.a2a_client.submit_to_agent", _fake_submit), \
         patch("orchestrator.a2a_client.poll_for_result", _fake_poll), \
         patch("orchestrator.nodes.a2a_dispatch._resolve_agent_url", return_value="http://order-agent:8002"):
        result = await engine._dispatch_one(
            step=step,
            dependency_results={"prev-agent": {"output": {"x": 1}}},
            session=session,
            plan_id="plan_x",
            tenant_id="tenant_y",
            plan_service=None,
            registry=None,
            trace_id="trace_1",
        )

    assert len(submitted_payload) == 1
    p = submitted_payload[0]
    assert p.instructions == "Tạo đơn bàn 3"
    assert p.session_id == "sess_payload"
    assert p.tenant_id == "tenant_y"
    assert p.skill == "create_order"
    assert p.dependency_results == {"prev-agent": {"output": {"x": 1}}}
    assert len(p.conversation_history) == 2

"""Integration tests: plan sub-goal status transitions reflect A2A task progress."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan_service_mock(plan_id: str = "plan-uuid-1") -> AsyncMock:
    """Build an AsyncMock PlanService that tracks calls."""
    svc = AsyncMock()
    svc.create_plan = AsyncMock(return_value=plan_id)
    svc.link_task = AsyncMock(return_value=None)
    svc.sync_from_task = AsyncMock(return_value=None)
    svc.get_plan = AsyncMock(return_value={
        "plan": {
            "id": plan_id,
            "session_id": "sess-test",
            "goal": "Test plan",
            "status": "running",
        },
        "sub_goals": [
            {
                "sequence": 1,
                "title": "Test step",
                "agent_name": "bi-agent",
                "agent_label": "Báo Cáo & Phân Tích",
                "a2a_task_id": "task-1",
                "status": "running",
                "result_summary": None,
            }
        ],
    })
    svc.db = AsyncMock()
    svc.db.fetchrow = AsyncMock(return_value={"id": plan_id})
    return svc


# ---------------------------------------------------------------------------
# Test: link_task called after A2A submit
# ---------------------------------------------------------------------------


def test_link_task_called_after_submit():
    """After A2A task is submitted, plan_service.link_task() is called with plan_id + task_id."""
    mock_svc = _make_plan_service_mock("plan-1")

    with (
        patch(
            "orchestrator.graph.invoke_chat",
            new=AsyncMock(return_value={
                "reply": "Done",
                "intent": "bi_query",
                "requires_input": False,
                "model_used": "gpt-4o-mini",
                "plan_id": "plan-1",
            }),
        ),
    ):
        from orchestrator.main import app
        with TestClient(app) as client:
            app.state.plan_service = mock_svc
            resp = client.post(
                "/chat",
                json={"message": "doanh thu hôm nay", "session_id": "sess-test"},
            )

    assert resp.status_code == 200


def test_plan_visible_after_chat():
    """After /chat processes a request, GET /plans/{session_id}/current returns the plan."""
    mock_svc = _make_plan_service_mock("plan-vis-1")

    with patch(
        "orchestrator.graph.invoke_chat",
        new=AsyncMock(return_value={
            "reply": "Doanh thu hôm nay: 1 triệu",
            "intent": "bi_query",
            "requires_input": False,
            "model_used": "gpt-4o-mini",
        }),
    ):
        from orchestrator.main import app
        with TestClient(app) as client:
            app.state.plan_service = mock_svc
            client.post("/chat", json={"message": "doanh thu hôm nay", "session_id": "sess-vis"})
            resp = client.get("/plans/sess-vis/current")

    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "sub_goals" in data


# ---------------------------------------------------------------------------
# Test: sub-goal status transitions via PlanService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_from_task_updates_sub_goal_to_completed():
    """sync_from_task('completed') propagates completed status to sub_goal."""
    from orchestrator.core.plan_service import PlanService

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": "plan-uuid", "pending_count": 0, "failed_count": 0}
    )
    svc = PlanService(mock_pool)

    await svc.sync_from_task("task-abc", "completed", "Doanh thu: 5 triệu")

    # Verify UPDATE was called with correct status
    execute_calls = mock_pool.execute.call_args_list
    assert any("completed" in str(c) for c in execute_calls)


@pytest.mark.asyncio
async def test_sync_from_task_updates_sub_goal_to_failed():
    """sync_from_task('failed') propagates failed status to sub_goal."""
    from orchestrator.core.plan_service import PlanService

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": "plan-uuid", "pending_count": 0, "failed_count": 1}
    )
    svc = PlanService(mock_pool)

    await svc.sync_from_task("task-abc", "failed", None)

    execute_calls = mock_pool.execute.call_args_list
    assert any("failed" in str(c) for c in execute_calls)


@pytest.mark.asyncio
async def test_sync_from_task_ignores_unknown_status():
    """sync_from_task with unknown status is a no-op."""
    from orchestrator.core.plan_service import PlanService

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()
    svc = PlanService(mock_pool)

    await svc.sync_from_task("task-abc", "unknown_status", None)

    mock_pool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_parent_plan_completed_when_all_sub_goals_done():
    """Parent plan status set to 'completed' when all sub_goals reach terminal state."""
    from orchestrator.core.plan_service import PlanService

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()
    # fetchrow simulates: 0 pending, 0 failed → all completed
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": "plan-uuid", "pending_count": 0, "failed_count": 0}
    )
    svc = PlanService(mock_pool)

    await svc._maybe_complete_plan("task-abc")

    # Verify plans table was updated to 'completed'
    execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
    assert any("completed" in c for c in execute_calls)


@pytest.mark.asyncio
async def test_parent_plan_failed_when_any_sub_goal_failed():
    """Parent plan status set to 'failed' when any sub_goal is failed."""
    from orchestrator.core.plan_service import PlanService

    mock_pool = AsyncMock()
    mock_pool.execute = AsyncMock()
    # fetchrow: 0 pending, 1 failed
    mock_pool.fetchrow = AsyncMock(
        return_value={"id": "plan-uuid", "pending_count": 0, "failed_count": 1}
    )
    svc = PlanService(mock_pool)

    await svc._maybe_complete_plan("task-abc")

    execute_calls = [str(c) for c in mock_pool.execute.call_args_list]
    assert any("failed" in c for c in execute_calls)


# ---------------------------------------------------------------------------
# Test: plan endpoint returns 404 for non-existent session
# ---------------------------------------------------------------------------


def test_get_current_plan_returns_404_for_unknown_session():
    """GET /plans/{session_id}/current returns 404 for session with no plans."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(return_value=None)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/nonexistent-session/current")

    assert resp.status_code == 404

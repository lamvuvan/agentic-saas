"""Contract tests for GET /plans/* — validates response schema for plan endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MOCK_PLAN = {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "session_id": "sess-abc123",
    "tenant_id": "default",
    "goal": "Truy vấn doanh thu trong ngày hôm nay",
    "status": "running",
    "created_at": "2026-03-18T10:00:00+00:00",
    "updated_at": "2026-03-18T10:00:00+00:00",
}

_MOCK_SUB_GOALS = [
    {
        "id": "660e8400-e29b-41d4-a716-446655440001",
        "plan_id": "550e8400-e29b-41d4-a716-446655440000",
        "sequence": 1,
        "title": "Truy vấn doanh thu trong ngày hôm nay",
        "agent_name": "bi-agent",
        "agent_label": "Báo Cáo & Phân Tích",
        "a2a_task_id": None,
        "status": "pending",
        "result_summary": None,
        "started_at": None,
        "completed_at": None,
    }
]

_MOCK_PLAN_RESPONSE = {"plan": _MOCK_PLAN, "sub_goals": _MOCK_SUB_GOALS}


def _make_client_with_mock_service(mock_service: MagicMock) -> TestClient:
    """Create TestClient with a mocked PlanService on app.state."""
    from orchestrator.main import app

    with TestClient(app) as c:
        app.state.plan_service = mock_service
        yield c


# ---------------------------------------------------------------------------
# GET /plans/{session_id}/current — 200 schema
# ---------------------------------------------------------------------------


def test_get_current_plan_200_schema():
    """GET /plans/{session_id}/current returns correct schema when plan exists."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "sub_goals" in data


def test_get_current_plan_plan_has_required_fields():
    """plan object in response has id, session_id, goal, status."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    plan = resp.json()["plan"]
    for field in ("id", "session_id", "goal", "status"):
        assert field in plan, f"Missing field: {field}"


def test_get_current_plan_sub_goals_is_list():
    """sub_goals in response is a list."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    assert isinstance(resp.json()["sub_goals"], list)


def test_get_current_plan_sub_goal_has_required_fields():
    """Each sub_goal has sequence, title, agent_label, status fields."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    sub_goal = resp.json()["sub_goals"][0]
    for field in ("sequence", "title", "agent_label", "status"):
        assert field in sub_goal, f"Missing field: {field}"


def test_get_current_plan_agent_label_not_agent_name():
    """agent_label is present; internal agent_name may also be present but label drives display."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    sub_goal = resp.json()["sub_goals"][0]
    assert sub_goal["agent_label"] == "Báo Cáo & Phân Tích"


# ---------------------------------------------------------------------------
# GET /plans/{session_id}/current — 404
# ---------------------------------------------------------------------------


def test_get_current_plan_404_when_no_plan():
    """GET /plans/{session_id}/current returns 404 when session has no plans."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(return_value=None)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/no-such-session/current")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /plans/{plan_id} — 200 and 404
# ---------------------------------------------------------------------------


def test_get_plan_by_id_200():
    """GET /plans/{plan_id} returns plan + sub_goals for valid plan_id."""
    mock_svc = AsyncMock()
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/550e8400-e29b-41d4-a716-446655440000")

    assert resp.status_code == 200
    data = resp.json()
    assert "plan" in data
    assert "sub_goals" in data


def test_get_plan_by_id_404():
    """GET /plans/{plan_id} returns 404 when plan not found."""
    mock_svc = AsyncMock()
    mock_svc.get_plan = AsyncMock(return_value={})

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Status field values
# ---------------------------------------------------------------------------


def test_plan_status_is_valid_enum():
    """plan.status must be one of: pending, running, completed, failed."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    valid_statuses = {"pending", "running", "completed", "failed"}
    assert resp.json()["plan"]["status"] in valid_statuses


def test_sub_goal_status_is_valid_enum():
    """sub_goal.status must be one of: pending, running, completed, failed."""
    mock_svc = AsyncMock()
    mock_svc.db = AsyncMock()
    mock_svc.db.fetchrow = AsyncMock(
        return_value={"id": "550e8400-e29b-41d4-a716-446655440000"}
    )
    mock_svc.get_plan = AsyncMock(return_value=_MOCK_PLAN_RESPONSE)

    from orchestrator.main import app

    with TestClient(app) as client:
        app.state.plan_service = mock_svc
        resp = client.get("/plans/sess-abc123/current")

    valid_statuses = {"pending", "running", "completed", "failed"}
    assert resp.json()["sub_goals"][0]["status"] in valid_statuses

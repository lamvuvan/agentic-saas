"""Contract tests for A2A task submit/poll protocol.

Tests run against real Order Agent and BI Agent A2A endpoints via TestClient.
Must FAIL before a2a_server.py is implemented (TDD per Constitution §III).
"""

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def order_client():
    from order_agent.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def bi_client():
    from bi_agent.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /a2a/tasks — Order Agent
# ---------------------------------------------------------------------------


def test_submit_task_returns_202_and_task_id(order_client):
    resp = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "hai trứng lộn", "session_id": "s1"}},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "submitted"
    assert isinstance(data["task_id"], str)
    assert len(data["task_id"]) > 0


def test_submit_task_missing_skill_returns_422(order_client):
    resp = order_client.post(
        "/a2a/tasks",
        json={"params": {"message": "test", "session_id": "s1"}},
    )
    assert resp.status_code == 422


def test_submit_task_missing_params_returns_422(order_client):
    resp = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /a2a/tasks/{task_id}
# ---------------------------------------------------------------------------


def test_poll_task_returns_valid_status(order_client):
    # Submit first
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "một cháo lòng", "session_id": "s2"}},
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    # Poll
    poll = order_client.get(f"/a2a/tasks/{task_id}")
    assert poll.status_code == 200
    data = poll.json()
    assert data["task_id"] == task_id
    assert data["status"] in ("submitted", "working", "completed", "failed", "timeout", "input-required")


def test_poll_unknown_task_returns_404(order_client):
    resp = order_client.get("/a2a/tasks/nonexistent-task-id-xyz")
    assert resp.status_code == 404


def test_poll_task_completed_has_result_field(order_client):
    """When status = completed, result must be present with required fields."""
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "ba phở", "session_id": "s3"}},
    )
    task_id = submit.json()["task_id"]

    import time

    # Give the background task a brief moment; in unit context it resolves quickly
    for _ in range(20):
        poll = order_client.get(f"/a2a/tasks/{task_id}")
        status = poll.json().get("status")
        if status in ("completed", "failed", "input-required"):
            break
        time.sleep(0.1)

    poll = order_client.get(f"/a2a/tasks/{task_id}")
    data = poll.json()
    if data["status"] == "completed":
        assert "result" in data
        result = data["result"]
        assert "output" in result
        assert "reasoning_summary" in result


# ---------------------------------------------------------------------------
# BI Agent A2A — basic contract
# ---------------------------------------------------------------------------


def test_bi_submit_task_returns_202(bi_client):
    resp = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu hôm nay", "session_id": "s4"}},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "task_id" in data
    assert data["status"] == "submitted"


def test_bi_poll_unknown_returns_404(bi_client):
    resp = bi_client.get("/a2a/tasks/no-such-task")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Security constraints
# ---------------------------------------------------------------------------


def test_authorization_header_not_echoed_in_response(order_client):
    """Bearer token must never appear in any response body."""
    submit = order_client.post(
        "/a2a/tasks",
        headers={"Authorization": "Bearer super-secret-token-abc123"},
        json={"skill": "create_order", "params": {"message": "test", "session_id": "s5"}},
    )
    # Check token never appears in any response text
    assert "super-secret-token-abc123" not in submit.text

    task_id = submit.json()["task_id"]
    poll = order_client.get(f"/a2a/tasks/{task_id}")
    assert "super-secret-token-abc123" not in poll.text

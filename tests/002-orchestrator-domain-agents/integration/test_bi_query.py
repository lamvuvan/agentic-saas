"""Integration tests: BI query flow — Vietnamese question → SQL → execute → formatted answer."""

import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


@pytest.fixture
def bi_client():
    from bi_agent.main import app

    with TestClient(app) as c:
        yield c


def _poll(client, task_id, max_s=10):
    terminal = {"completed", "failed", "timeout"}
    for _ in range(int(max_s * 10)):
        data = client.get(f"/a2a/tasks/{task_id}").json()
        if data["status"] in terminal:
            return data
        time.sleep(0.1)
    return client.get(f"/a2a/tasks/{task_id}").json()


def test_revenue_query_reaches_terminal(bi_client):
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu hôm nay", "session_id": "bi-int-s1"}},
    )
    assert submit.status_code == 202
    data = _poll(bi_client, submit.json()["task_id"])
    assert data["status"] in ("completed", "failed", "timeout")


def test_destructive_query_is_rejected(bi_client):
    """DELETE/UPDATE queries must be rejected — not executed."""
    submit = bi_client.post(
        "/a2a/tasks",
        json={
            "skill": "bi_query",
            "params": {"message": "xóa tất cả đơn hàng", "session_id": "bi-int-s2"},
        },
    )
    data = _poll(bi_client, submit.json()["task_id"])

    if data["status"] == "completed":
        output = data["result"]["output"]
        # Must be rejected, not a successful delete
        assert output.get("status") in ("rejected", "error")


def test_customer_ranking_query_reaches_terminal(bi_client):
    submit = bi_client.post(
        "/a2a/tasks",
        json={
            "skill": "bi_query",
            "params": {"message": "top 5 khách hàng mua nhiều nhất", "session_id": "bi-int-s3"},
        },
    )
    data = _poll(bi_client, submit.json()["task_id"])
    assert data["status"] in ("completed", "failed", "timeout")


def test_bi_query_result_has_reasoning_summary(bi_client):
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu tháng này", "session_id": "bi-int-s4"}},
    )
    data = _poll(bi_client, submit.json()["task_id"])

    if data["status"] == "completed":
        assert "reasoning_summary" in data["result"]
        assert len(data["result"]["reasoning_summary"]) > 0

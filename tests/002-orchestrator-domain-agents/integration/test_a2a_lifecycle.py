"""Integration test: A2A task lifecycle submitted→working→completed.

Tests the full submit+poll cycle against Domain Agent TestClient instances.
"""

import time

import pytest
from fastapi.testclient import TestClient


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
# Order Agent lifecycle
# ---------------------------------------------------------------------------


def test_order_task_lifecycle_submitted_to_terminal(order_client):
    """Task should transition from submitted through to a terminal state."""
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "hai trứng lộn", "session_id": "life-s1"}},
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    # Immediately after submit, status is submitted or working
    poll = order_client.get(f"/a2a/tasks/{task_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] in ("submitted", "working")

    # Poll until terminal (max 5s in test environment)
    terminal = {"completed", "failed", "timeout", "input-required"}
    reached_terminal = False
    for _ in range(50):
        poll = order_client.get(f"/a2a/tasks/{task_id}")
        if poll.json()["status"] in terminal:
            reached_terminal = True
            break
        time.sleep(0.1)

    assert reached_terminal, f"Task never reached terminal state: {poll.json()}"


def test_order_task_task_id_is_uuid(order_client):
    import re

    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "test", "session_id": "life-s2"}},
    )
    task_id = submit.json()["task_id"]
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    assert uuid_pattern.match(task_id), f"task_id is not a valid UUID v4: {task_id}"


def test_order_task_poll_has_timestamps(order_client):
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "test", "session_id": "life-s3"}},
    )
    task_id = submit.json()["task_id"]
    poll = order_client.get(f"/a2a/tasks/{task_id}")
    data = poll.json()
    assert "created_at" in data
    assert "updated_at" in data


# ---------------------------------------------------------------------------
# BI Agent lifecycle
# ---------------------------------------------------------------------------


def test_bi_task_lifecycle_submitted_to_terminal(bi_client):
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu hôm nay", "session_id": "life-s4"}},
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    terminal = {"completed", "failed", "timeout"}
    reached_terminal = False
    for _ in range(50):
        poll = bi_client.get(f"/a2a/tasks/{task_id}")
        if poll.json()["status"] in terminal:
            reached_terminal = True
            break
        time.sleep(0.1)

    assert reached_terminal, f"BI task never reached terminal state: {poll.json()}"


def test_different_tasks_have_different_ids(order_client):
    ids = set()
    for i in range(5):
        submit = order_client.post(
            "/a2a/tasks",
            json={"skill": "create_order", "params": {"message": f"test {i}", "session_id": f"life-s{i+10}"}},
        )
        ids.add(submit.json()["task_id"])
    assert len(ids) == 5, "Task IDs must be unique"

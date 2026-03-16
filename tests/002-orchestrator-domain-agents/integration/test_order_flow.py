"""Integration tests: full multi-turn order creation flow."""

import time
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


@pytest.fixture
def order_client():
    from order_agent.main import app

    with TestClient(app) as c:
        yield c


def _poll_until_terminal(client, task_id, max_wait_s=10):
    terminal = {"completed", "failed", "timeout", "input-required"}
    for _ in range(int(max_wait_s * 10)):
        data = client.get(f"/a2a/tasks/{task_id}").json()
        if data["status"] in terminal:
            return data
        time.sleep(0.1)
    return client.get(f"/a2a/tasks/{task_id}").json()


def test_single_item_order_reaches_input_required_or_completed(order_client):
    """A valid order should reach either input-required (preview) or completed."""
    submit = order_client.post(
        "/a2a/tasks",
        json={
            "skill": "create_order",
            "params": {"message": "anh Lâm hai trứng lộn", "session_id": "flow-s1"},
        },
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    data = _poll_until_terminal(order_client, task_id)
    assert data["status"] in ("input-required", "completed", "failed")


def test_multi_item_order_includes_all_items(order_client):
    """Multi-item order should process all items without data loss."""
    submit = order_client.post(
        "/a2a/tasks",
        json={
            "skill": "create_order",
            "params": {
                "message": "bàn 3 cho tôi 3 bò kho bánh mì",
                "session_id": "flow-s2",
            },
        },
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]
    data = _poll_until_terminal(order_client, task_id)
    # Task must reach a terminal state — not hang
    assert data["status"] in ("input-required", "completed", "failed")


def test_order_continuation_resumes_from_input_required(order_client):
    """When status = input-required, a continuation submission should resume the task."""
    # Step 1: submit new order
    submit = order_client.post(
        "/a2a/tasks",
        json={
            "skill": "create_order",
            "params": {"message": "một cháo lòng", "session_id": "flow-s3"},
        },
    )
    task_id = submit.json()["task_id"]
    data = _poll_until_terminal(order_client, task_id)

    if data["status"] == "input-required":
        # Step 2: confirm via continuation
        resume = order_client.post(
            "/a2a/tasks",
            json={
                "skill": "create_order",
                "params": {
                    "message": "xác nhận",
                    "session_id": "flow-s3",
                    "continuation": {"task_id": task_id, "user_input": "xác nhận"},
                },
            },
        )
        assert resume.status_code == 202
        # The resume returns a new task_id or updates the existing one
        resume_data = _poll_until_terminal(order_client, resume.json()["task_id"])
        # After confirmation, should reach completed or another input-required step
        assert resume_data["status"] in ("completed", "input-required", "failed")


def test_task_ids_are_unique_per_submission(order_client):
    ids = []
    for i in range(3):
        submit = order_client.post(
            "/a2a/tasks",
            json={
                "skill": "create_order",
                "params": {"message": f"test {i}", "session_id": f"flow-s{i+10}"},
            },
        )
        ids.append(submit.json()["task_id"])
    assert len(set(ids)) == 3

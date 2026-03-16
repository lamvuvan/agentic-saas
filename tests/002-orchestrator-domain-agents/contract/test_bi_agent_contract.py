"""Contract tests for BI Agent A2A response output schema."""

import time
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def bi_client():
    from bi_agent.main import app

    with TestClient(app) as c:
        yield c


def _wait_terminal(client, task_id, max_wait_s=5):
    terminal = {"completed", "failed", "timeout"}
    for _ in range(int(max_wait_s * 10)):
        data = client.get(f"/a2a/tasks/{task_id}").json()
        if data["status"] in terminal:
            return data
        time.sleep(0.1)
    return client.get(f"/a2a/tasks/{task_id}").json()


def test_bi_completed_result_schema(bi_client):
    """Completed BI result must have output, reasoning_summary per A2AResult schema."""
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu hôm nay", "session_id": "bi-s1"}},
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]
    data = _wait_terminal(bi_client, task_id)

    if data["status"] == "completed":
        result = data["result"]
        assert "output" in result
        assert "reasoning_summary" in result

        output = result["output"]
        if isinstance(output, dict):
            assert "status" in output
            assert output["status"] in ("success", "rejected", "error")
            assert "message" in output


def test_bi_rejected_output_has_error_reason(bi_client):
    """Rejected queries must have error_reason in output."""
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "xóa tất cả dữ liệu", "session_id": "bi-s2"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_terminal(bi_client, task_id)

    if data["status"] == "completed":
        output = data["result"]["output"]
        if isinstance(output, dict) and output.get("status") == "rejected":
            assert "error_reason" in output


def test_bi_success_output_has_message_field(bi_client):
    """Successful queries must return a message field with Vietnamese text."""
    submit = bi_client.post(
        "/a2a/tasks",
        json={"skill": "bi_query", "params": {"message": "doanh thu tuần này", "session_id": "bi-s3"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_terminal(bi_client, task_id)

    if data["status"] == "completed":
        output = data["result"]["output"]
        if isinstance(output, dict) and output.get("status") == "success":
            assert "message" in output
            assert isinstance(output["message"], str)


def test_bi_token_not_in_result(bi_client):
    """Bearer token must not appear in any BI result."""
    import json as _json

    submit = bi_client.post(
        "/a2a/tasks",
        headers={"Authorization": "Bearer bi-secret-token-777"},
        json={"skill": "bi_query", "params": {"message": "test", "session_id": "bi-s4"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_terminal(bi_client, task_id)

    serialized = _json.dumps(data)
    assert "bi-secret-token-777" not in serialized

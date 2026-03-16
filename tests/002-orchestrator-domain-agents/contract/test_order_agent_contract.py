"""Contract tests for Order Agent A2A response output schema."""

import pytest
from fastapi.testclient import TestClient
import time


@pytest.fixture
def order_client():
    from order_agent.main import app

    with TestClient(app) as c:
        yield c


def _wait_for_terminal(client, task_id, max_wait_s=5):
    terminal = {"completed", "failed", "timeout", "input-required"}
    for _ in range(int(max_wait_s * 10)):
        data = client.get(f"/a2a/tasks/{task_id}").json()
        if data["status"] in terminal:
            return data
        time.sleep(0.1)
    return client.get(f"/a2a/tasks/{task_id}").json()


def test_completed_result_has_output_and_reasoning(order_client):
    """Completed tasks must have output and reasoning_summary in result."""
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "hai trứng lộn", "session_id": "oa-s1"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(order_client, task_id)

    if data["status"] == "completed":
        assert "result" in data
        result = data["result"]
        assert "output" in result
        assert "reasoning_summary" in result
        assert isinstance(result["reasoning_summary"], str)
        assert len(result["reasoning_summary"]) > 0


def test_input_required_has_input_request_field(order_client):
    """When status = input-required, input_request must be present."""
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "bàn 3 hai bò kho", "session_id": "oa-s2"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(order_client, task_id)

    if data["status"] == "input-required":
        assert "input_request" in data
        assert isinstance(data["input_request"], str)
        assert len(data["input_request"]) > 0


def test_failed_result_has_error_field(order_client):
    """Failed tasks must have an error message."""
    # Submit with minimal params to potentially trigger a failure path
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "", "session_id": "oa-s3"}},
    )
    # Even if this doesn't fail, the schema should be valid
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(order_client, task_id)

    if data["status"] == "failed":
        assert "error" in data
        assert isinstance(data["error"], str)


def test_result_output_matches_order_contract_schema(order_client):
    """When completed, output must have status field."""
    submit = order_client.post(
        "/a2a/tasks",
        json={"skill": "create_order", "params": {"message": "một cháo lòng", "session_id": "oa-s4"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(order_client, task_id)

    if data["status"] == "completed":
        output = data["result"]["output"]
        # Per contracts/order-agent-response.json: status is required
        if isinstance(output, dict):
            assert "status" in output


def test_result_has_no_bearer_token(order_client):
    """Token must never appear in result payload."""
    submit = order_client.post(
        "/a2a/tasks",
        headers={"Authorization": "Bearer contract-test-secret-99"},
        json={"skill": "create_order", "params": {"message": "test", "session_id": "oa-s5"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(order_client, task_id)

    import json as _json
    serialized = _json.dumps(data)
    assert "contract-test-secret-99" not in serialized

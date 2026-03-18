"""Contract tests for Customer Agent A2A server — T128.

Validates:
- POST /a2a/tasks 202 returns task_id + status
- GET /a2a/tasks/{id} returns valid status enum
- GET /.well-known/agent.json returns agent card with required skills
- Bearer token is never echoed in any response field
"""

from __future__ import annotations

import json as _json
import sys
import time
from unittest.mock import MagicMock

for _mod in ("openai", "openai.types", "faiss", "sentence_transformers", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def customer_client():
    from customer_agent.main import app

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


# ---------------------------------------------------------------------------
# T128.1 — POST /a2a/tasks returns 202 with task_id and status
# ---------------------------------------------------------------------------


def test_submit_task_returns_202(customer_client):
    """POST /a2a/tasks must return HTTP 202 with task_id and initial status."""
    resp = customer_client.post(
        "/a2a/tasks",
        json={"skill": "lookup_customer", "params": {"message": "tìm khách Lâm"}},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "task_id" in body
    assert "status" in body
    assert isinstance(body["task_id"], str)
    assert len(body["task_id"]) > 0


# ---------------------------------------------------------------------------
# T128.2 — GET /a2a/tasks/{id} returns valid status enum
# ---------------------------------------------------------------------------


def test_poll_status_returns_valid_enum(customer_client):
    """GET /a2a/tasks/{id} must return a valid TaskStatus value."""
    submit = customer_client.post(
        "/a2a/tasks",
        json={"skill": "lookup_customer", "params": {"message": "tìm anh Hùng"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(customer_client, task_id)

    valid_statuses = {"submitted", "working", "completed", "failed", "timeout", "input-required"}
    assert data["status"] in valid_statuses


# ---------------------------------------------------------------------------
# T128.3 — 404 for unknown task id
# ---------------------------------------------------------------------------


def test_unknown_task_returns_404(customer_client):
    """GET /a2a/tasks/{id} with unknown ID must return 404."""
    resp = customer_client.get("/a2a/tasks/nonexistent-task-id-00000000")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T128.4 — Agent card has required skills
# ---------------------------------------------------------------------------


def test_agent_card_has_required_skills(customer_client):
    """GET /.well-known/agent.json must expose lookup_customer, create_customer, update_customer skills."""
    resp = customer_client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()

    assert card["name"] == "Customer Agent"
    skill_ids = [s["id"] for s in card.get("skills", [])]
    assert "lookup_customer" in skill_ids
    assert "create_customer" in skill_ids
    assert "update_customer" in skill_ids


# ---------------------------------------------------------------------------
# T128.5 — Agent card has a2a_endpoint field
# ---------------------------------------------------------------------------


def test_agent_card_has_a2a_endpoint(customer_client):
    """Agent card must have a2a_endpoint field for AgentRegistry discovery."""
    resp = customer_client.get("/.well-known/agent.json")
    card = resp.json()
    assert "a2a_endpoint" in card or "url" in card


# ---------------------------------------------------------------------------
# T128.6 — Bearer token never echoed
# ---------------------------------------------------------------------------


def test_bearer_token_not_in_response(customer_client):
    """Bearer token must never appear in any response field."""
    secret = "contract-test-customer-secret-99"
    submit = customer_client.post(
        "/a2a/tasks",
        headers={"Authorization": f"Bearer {secret}"},
        json={"skill": "lookup_customer", "params": {"message": "test token safety"}},
    )
    task_id = submit.json()["task_id"]
    data = _wait_for_terminal(customer_client, task_id)

    serialized = _json.dumps(data)
    assert secret not in serialized

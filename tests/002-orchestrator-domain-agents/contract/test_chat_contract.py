"""Contract tests for POST /chat — validates response schema against contracts/chat.json."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch


@pytest.fixture
def orch_client():
    from orchestrator.main import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 200 response — required fields
# ---------------------------------------------------------------------------


def test_chat_200_has_required_fields(orch_client):
    """POST /chat 200 response must contain session_id, reply, intent, trace_id."""
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "Xin chào!",
        "intent": "chitchat",
        "requires_input": False,
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post("/chat", json={"message": "xin chào"})

    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert "reply" in data
    assert "intent" in data
    assert "trace_id" in data


def test_chat_200_intent_is_valid_enum(orch_client):
    valid_intents = {"order", "bi_query", "chitchat", "unknown"}
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "Tôi hiểu rồi",
        "intent": "order",
        "requires_input": True,
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post("/chat", json={"message": "hai trứng lộn"})

    assert resp.json()["intent"] in valid_intents


def test_chat_200_requires_input_is_boolean(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "Xác nhận đơn hàng?",
        "intent": "order",
        "requires_input": True,
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post("/chat", json={"message": "bàn 3 ba bò kho"})

    data = resp.json()
    assert isinstance(data.get("requires_input"), bool)


def test_chat_200_session_id_returned(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "Test",
        "intent": "chitchat",
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post(
            "/chat",
            json={"message": "hello", "session_id": "my-custom-session"},
        )

    assert resp.json()["session_id"] == "my-custom-session"


def test_chat_200_metadata_has_duration_ms(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "OK",
        "intent": "chitchat",
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post("/chat", json={"message": "test"})

    metadata = resp.json().get("metadata", {})
    assert "duration_ms" in metadata
    assert isinstance(metadata["duration_ms"], int)


# ---------------------------------------------------------------------------
# 422 — validation error
# ---------------------------------------------------------------------------


def test_chat_422_empty_message(orch_client):
    resp = orch_client.post("/chat", json={"message": ""})
    assert resp.status_code == 422


def test_chat_422_missing_message(orch_client):
    resp = orch_client.post("/chat", json={})
    assert resp.status_code == 422


def test_chat_422_message_too_long(orch_client):
    resp = orch_client.post("/chat", json={"message": "x" * 4097})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_chat_token_not_in_response(orch_client):
    """Bearer token must never appear in the response body."""
    with patch("orchestrator.graph.invoke_chat", new=AsyncMock(return_value={
        "reply": "OK",
        "intent": "chitchat",
        "model_used": "gpt-4o-mini",
    })):
        resp = orch_client.post(
            "/chat",
            headers={"Authorization": "Bearer ultra-secret-token-99999"},
            json={"message": "xin chào"},
        )

    assert "ultra-secret-token-99999" not in resp.text

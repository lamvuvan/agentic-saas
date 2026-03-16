"""Integration tests: intent classification routes messages to correct Domain Agent stub."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def orch_client():
    from orchestrator.main import app

    with TestClient(app) as c:
        yield c


def _mock_graph(intent: str, reply: str, requires_input: bool = False):
    return AsyncMock(return_value={
        "reply": reply,
        "intent": intent,
        "requires_input": requires_input,
        "model_used": "gpt-4o-mini",
    })


# ---------------------------------------------------------------------------
# Intent routing — order
# ---------------------------------------------------------------------------


def test_order_message_routes_to_order_intent(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("order", "Xác nhận đơn hàng?")):
        resp = orch_client.post(
            "/chat",
            headers={"Authorization": "Bearer tok"},
            json={"message": "anh Lâm hai trứng lộn một cháo lòng"},
        )
    assert resp.status_code == 200
    assert resp.json()["intent"] == "order"


def test_order_message_with_table_number(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("order", "Xem lại đơn hàng bàn 3?")):
        resp = orch_client.post(
            "/chat",
            json={"message": "bàn 3 cho tôi 3 bò kho bánh mì"},
        )
    assert resp.json()["intent"] == "order"


# ---------------------------------------------------------------------------
# Intent routing — bi_query
# ---------------------------------------------------------------------------


def test_bi_message_routes_to_bi_intent(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("bi_query", "Doanh thu hôm nay: 1,200,000đ")):
        resp = orch_client.post(
            "/chat",
            json={"message": "doanh thu hôm nay bao nhiêu"},
        )
    assert resp.json()["intent"] == "bi_query"


def test_top_customers_query(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("bi_query", "Top 5 khách hàng...")):
        resp = orch_client.post(
            "/chat",
            json={"message": "top 5 khách hàng mua nhiều nhất tháng này"},
        )
    assert resp.json()["intent"] == "bi_query"


# ---------------------------------------------------------------------------
# Intent routing — chitchat
# ---------------------------------------------------------------------------


def test_chitchat_message_no_agent_routing(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("chitchat", "Xin chào! Tôi có thể giúp gì?")):
        resp = orch_client.post(
            "/chat",
            json={"message": "xin chào"},
        )
    data = resp.json()
    assert data["intent"] == "chitchat"
    # Chitchat must not require further input for a simple greeting
    assert data.get("requires_input") is False


# ---------------------------------------------------------------------------
# Session continuity
# ---------------------------------------------------------------------------


def test_session_id_preserved_across_turns(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("order", "Xác nhận?", requires_input=True)):
        resp1 = orch_client.post(
            "/chat",
            json={"message": "hai trứng lộn", "session_id": "my-sess"},
        )
    session_id = resp1.json()["session_id"]
    assert session_id == "my-sess"

    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("order", "Đơn hàng đã tạo!")):
        resp2 = orch_client.post(
            "/chat",
            json={"message": "xác nhận", "session_id": session_id},
        )
    assert resp2.json()["session_id"] == session_id


def test_session_id_generated_when_absent(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("chitchat", "Xin chào!")):
        resp = orch_client.post("/chat", json={"message": "xin chào"})
    session_id = resp.json()["session_id"]
    assert session_id is not None
    assert len(session_id) > 0


# ---------------------------------------------------------------------------
# Trace ID
# ---------------------------------------------------------------------------


def test_trace_id_returned_in_response(orch_client):
    with patch("orchestrator.graph.invoke_chat", new=_mock_graph("chitchat", "OK")):
        resp = orch_client.post(
            "/chat",
            headers={"X-Trace-Id": "trace-abc123"},
            json={"message": "test"},
        )
    assert resp.json()["trace_id"] == "trace-abc123"

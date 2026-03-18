"""Integration tests for OrchestratorMemoryService — T099.

Tests the full OrchestratorMemoryService lifecycle using an asyncpg mock:
- store_pattern upserts a routing_pattern row
- retrieve_patterns returns it ranked first (confidence DESC, usage_count DESC)
- find_similar_plans queries plans + plan_sub_goals (completed plans only)
- extract_and_store calls store_pattern for each extracted fact
- graceful degradation returns empty list / no-op when db=None
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.core.orchestrator_memory import OrchestratorMemoryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(fetch_rows=None):
    """Build a minimal asyncpg.Pool mock."""
    db = MagicMock()
    db.fetch = AsyncMock(return_value=fetch_rows or [])
    db.execute = AsyncMock(return_value=None)
    return db


def _make_row(**kwargs):
    return kwargs


# ---------------------------------------------------------------------------
# T099.1 — store_pattern upserts a routing_pattern row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_pattern_upserts_row():
    db = _make_db()
    svc = OrchestratorMemoryService(db)

    await svc.store_pattern(
        tenant_id="t1",
        memory_type="routing_pattern",
        key="order+bi_combined",
        content="Request vừa tạo đơn vừa xem BI → chạy BI trước rồi mới Order",
        confidence=0.85,
    )

    db.execute.assert_awaited_once()
    sql = db.execute.call_args[0][0]
    assert "INSERT INTO orchestrator_memory" in sql
    assert "ON CONFLICT" in sql
    assert "GREATEST(orchestrator_memory.confidence" in sql
    args = db.execute.call_args[0]
    assert args[1] == "t1"         # tenant_id
    assert args[2] == "routing_pattern"
    assert args[3] == "order+bi_combined"


# ---------------------------------------------------------------------------
# T099.2 — retrieve_patterns returns rows ordered by confidence DESC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_patterns_returns_ranked_rows():
    row = _make_row(
        memory_type="routing_pattern",
        key="order+bi_combined",
        content="BI trước rồi mới Order",
        confidence=0.85,
    )
    db = _make_db(fetch_rows=[row])
    svc = OrchestratorMemoryService(db)

    results = await svc.retrieve_patterns(
        tenant_id="t1",
        intent_class="order",
        request_summary="đặt hàng và xem doanh thu",
        limit=4,
    )

    assert len(results) == 1
    assert results[0]["memory_type"] == "routing_pattern"
    assert "Order" in results[0]["content"]

    sql = db.fetch.call_args[0][0]
    assert "confidence DESC" in sql
    assert "usage_count DESC" in sql


# ---------------------------------------------------------------------------
# T099.3 — find_similar_plans queries completed plans table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_plans_queries_completed_plans():
    plan_row = _make_row(
        goal="Xem doanh thu hôm nay",
        user_message="doanh thu hôm nay",
        sub_goals=json.dumps([{"sequence": 1, "agent_name": "bi-agent", "title": "Truy vấn doanh thu"}]),
    )
    db = _make_db(fetch_rows=[plan_row])
    svc = OrchestratorMemoryService(db)

    results = await svc.find_similar_plans(
        tenant_id="t1",
        user_message="doanh thu",
        limit=3,
    )

    assert len(results) == 1
    assert "doanh thu" in results[0]["user_message"]

    sql = db.fetch.call_args[0][0]
    assert "plans" in sql
    assert "plan_sub_goals" in sql
    assert "'completed'" in sql


# ---------------------------------------------------------------------------
# T099.4 — extract_and_store calls store_pattern for each extracted fact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_store_calls_store_pattern():
    db = _make_db()
    svc = OrchestratorMemoryService(db)

    facts = [
        {
            "memory_type": "routing_pattern",
            "key": "bi_then_order",
            "content": "BI trước → Order sau",
            "confidence": 0.85,
        },
        {
            "memory_type": "plan_template",
            "key": "daily_revenue",
            "content": "sub_goals: [bi-agent: doanh thu]",
            "confidence": 0.80,
        },
    ]

    with patch.object(svc, "_call_extract_llm", new=AsyncMock(return_value=facts)):
        await svc.extract_and_store(
            tenant_id="t1",
            plan={"goal": "Xem BI và tạo đơn", "id": "plan-1"},
            sub_goals=[{"agent_name": "bi-agent"}, {"agent_name": "order-agent"}],
        )

    # store_pattern was called once per fact (2 execute calls)
    assert db.execute.await_count == 2


# ---------------------------------------------------------------------------
# T099.5 — graceful degradation: db=None returns empty without raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation_no_db():
    svc = OrchestratorMemoryService(None)

    results = await svc.retrieve_patterns("t1", "order", "test summary")
    assert results == []

    plans = await svc.find_similar_plans("t1", "test message")
    assert plans == []

    # store_pattern must not raise
    await svc.store_pattern("t1", "routing_pattern", "key", "content")

    # extract_and_store must not raise (no LLM call when db=None)
    await svc.extract_and_store("t1", {"goal": "test"}, [])


# ---------------------------------------------------------------------------
# T099.6 — extract_and_store swallows LLM errors (best-effort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_store_swallows_llm_error():
    db = _make_db()
    svc = OrchestratorMemoryService(db)

    with patch.object(svc, "_call_extract_llm", new=AsyncMock(side_effect=RuntimeError("LLM down"))):
        # Must not raise
        await svc.extract_and_store("t1", {"goal": "test"}, [])

    db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# T099.7 — retrieve_patterns returns empty list on DB error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_patterns_returns_empty_on_db_error():
    db = MagicMock()
    db.fetch = AsyncMock(side_effect=RuntimeError("connection refused"))
    svc = OrchestratorMemoryService(db)

    results = await svc.retrieve_patterns("t1", "order", "test")
    assert results == []


# ---------------------------------------------------------------------------
# T099.8 — store_pattern with None db is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_pattern_none_db_noop():
    svc = OrchestratorMemoryService(None)
    # Must not raise
    await svc.store_pattern("t1", "routing_pattern", "key", "content", 0.9)

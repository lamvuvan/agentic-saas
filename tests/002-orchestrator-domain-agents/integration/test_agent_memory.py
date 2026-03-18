"""Integration tests for MemoryService — T089.

Tests the full MemoryService lifecycle using an in-memory asyncpg mock:
- store product_alias → retrieve returns it ranked first
- find_similar_tasks returns past successes
- record_task inserts a history row
- graceful degradation: when db=None returns empty results without raising
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from shared.memory_service import MemoryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(fetch_rows=None, execute_ok=True):
    """Build a minimal asyncpg.Pool mock."""
    db = MagicMock()
    rows = fetch_rows or []
    db.fetch = AsyncMock(return_value=rows)
    db.execute = AsyncMock(return_value=None)
    return db


def _make_row(**kwargs):
    """Build a dict-like row compatible with asyncpg Record access."""
    return kwargs


# ---------------------------------------------------------------------------
# T089.1 — store saves a product_alias and retrieve returns it first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_and_retrieve_product_alias():
    row = _make_row(
        id="uuid-1",
        memory_type="product_alias",
        key="ba đen",
        content="cafe đen đá size L",
        confidence=0.95,
        usage_count=0,
    )
    db = _make_db(fetch_rows=[row])
    svc = MemoryService(db, "order-agent")

    # Store the alias
    await svc.store(
        tenant_id="t1",
        memory_type="product_alias",
        key="ba đen",
        content="cafe đen đá size L",
        confidence=0.95,
    )

    # Verify INSERT ... ON CONFLICT was called
    db.execute.assert_awaited_once()
    call_args = db.execute.call_args[0]
    assert "INSERT INTO agent_memory" in call_args[0]
    assert "ON CONFLICT" in call_args[0]
    # args: $1=agent_name, $2=tenant_id, $3=memory_type, $4=key, $5=content, $6=confidence
    assert call_args[5] == "cafe đen đá size L"

    # Retrieve and verify returned first
    results = await svc.retrieve(
        tenant_id="t1",
        query_context={"keyword": "ba đen"},
        limit=5,
    )
    assert len(results) == 1
    assert results[0]["memory_type"] == "product_alias"
    assert results[0]["content"] == "cafe đen đá size L"


# ---------------------------------------------------------------------------
# T089.2 — retrieve queries with correct ORDER BY and bumps usage_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_bumps_usage_count():
    row = _make_row(
        id="uuid-42",
        memory_type="glossary_fix",
        key="doanh thu",
        content="= net_revenue column",
        confidence=0.9,
        usage_count=3,
    )
    db = _make_db(fetch_rows=[row])
    svc = MemoryService(db, "bi-agent")

    await svc.retrieve(
        tenant_id="t1",
        query_context={"keyword": "doanh thu"},
        limit=5,
    )

    # fetch was called with correct query
    fetch_call = db.fetch.call_args[0]
    assert "ORDER BY confidence DESC, usage_count DESC" in fetch_call[0]

    # _bump_usage called — second execute call with UPDATE
    assert db.execute.await_count == 1
    bump_call = db.execute.call_args[0]
    assert "UPDATE agent_memory" in bump_call[0]
    assert "uuid-42" in bump_call  # id passed as positional arg


# ---------------------------------------------------------------------------
# T089.3 — find_similar_tasks returns past successful tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_tasks_returns_successes():
    task_row = _make_row(
        input_summary="order: ba đen 1 ly",
        key_decisions=json.dumps({"order__create_order": {"items": []}}),
        learnings='[{"memory_type":"product_alias","key":"ba đen","content":"cafe đen đá size L","confidence":0.9}]',
        duration_ms=1200,
    )
    db = _make_db(fetch_rows=[task_row])
    svc = MemoryService(db, "order-agent")

    results = await svc.find_similar_tasks(
        tenant_id="t1",
        skill="create_order",
        input_summary="order: ba đen",
        limit=3,
    )

    assert len(results) == 1
    assert "ba đen" in results[0]["input_summary"]

    # Verify query filters by outcome='success'
    fetch_call_sql = db.fetch.call_args[0][0]
    assert "outcome" in fetch_call_sql and "'success'" in fetch_call_sql


# ---------------------------------------------------------------------------
# T089.4 — record_task inserts into agent_task_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_task_inserts_history_row():
    db = _make_db()
    svc = MemoryService(db, "order-agent")

    await svc.record_task(
        tenant_id="t1",
        plan_id="plan-uuid-1",
        skill="create_order",
        input_summary="order: customer_id=cust_123, msg=ba đen 1 ly",
        outcome="success",
        key_decisions={"order__create_order": {"items": ["ba_den"]}},
        learnings='[{"memory_type":"product_alias","key":"ba đen","content":"cafe đen đá","confidence":0.9}]',
        duration_ms=1500,
    )

    db.execute.assert_awaited_once()
    sql_call = db.execute.call_args[0][0]
    assert "INSERT INTO agent_task_history" in sql_call
    # Verify all required columns are in the INSERT
    assert "input_summary" in sql_call
    assert "outcome" in sql_call
    assert "key_decisions" in sql_call


# ---------------------------------------------------------------------------
# T089.5 — graceful degradation: db=None returns empty without raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation_no_db():
    svc = MemoryService(None, "order-agent")

    results = await svc.retrieve(
        tenant_id="t1",
        query_context={"keyword": "anything"},
    )
    assert results == []

    similar = await svc.find_similar_tasks(
        tenant_id="t1",
        skill="create_order",
        input_summary="test",
    )
    assert similar == []

    # store and record_task should not raise
    await svc.store("t1", "product_alias", "key", "content")
    await svc.record_task(
        tenant_id="t1",
        plan_id=None,
        skill="create_order",
        input_summary="safe summary",
        outcome="success",
        key_decisions={},
        learnings="",
        duration_ms=0,
    )


# ---------------------------------------------------------------------------
# T089.6 — UPSERT ON CONFLICT takes GREATEST confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_uses_greatest_confidence():
    db = _make_db()
    svc = MemoryService(db, "order-agent")

    await svc.store(
        tenant_id="t1",
        memory_type="product_alias",
        key="ba đen",
        content="cafe đen đá size L",
        confidence=0.7,
    )
    execute_sql = db.execute.call_args[0][0]
    assert "GREATEST(agent_memory.confidence" in execute_sql


# ---------------------------------------------------------------------------
# T089.7 — retrieve returns empty list on DB error (no exception propagated)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_returns_empty_on_db_error():
    db = MagicMock()
    db.fetch = AsyncMock(side_effect=RuntimeError("connection refused"))
    svc = MemoryService(db, "order-agent")

    results = await svc.retrieve("t1", {"keyword": "test"})
    assert results == []


# ---------------------------------------------------------------------------
# T089.8 — MemoryAwareReActLoop skips memory when memory=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_loop_skips_memory_when_none():
    """MemoryAwareReActLoop.run() with memory=None calls run_order_graph unchanged."""
    from order_agent.core.react_loop import MemoryAwareReActLoop

    mock_result = {
        "status": "input-required",
        "input_request": "Xác nhận đơn hàng?",
        "reasoning_summary": "Preview built",
        "tool_calls": [],
    }

    with patch("order_agent.graph.run_order_graph", new=AsyncMock(return_value=mock_result)):
        loop = MemoryAwareReActLoop(skill="create_order")
        redis_mock = MagicMock()
        result = await loop.run(
            task_id="task-1",
            params={"message": "ba đen 1 ly"},
            redis=redis_mock,
            memory=None,
        )

    assert result["status"] == "input-required"

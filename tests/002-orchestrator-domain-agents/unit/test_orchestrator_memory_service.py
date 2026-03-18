"""Unit tests for OrchestratorMemoryService — T100.

Tests:
- retrieve_patterns SQL orders by confidence DESC, usage_count DESC
- UPSERT ON CONFLICT uses GREATEST(confidence)
- extract_and_store swallows all errors (best-effort)
- store_pattern with None db is a no-op
- record_pattern stores correct tenant_id, memory_type, key
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.core.orchestrator_memory import OrchestratorMemoryService


# ---------------------------------------------------------------------------
# T100.1 — retrieve_patterns SQL orders by confidence DESC, usage_count DESC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_patterns_sql_ordering():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    svc = OrchestratorMemoryService(db)

    await svc.retrieve_patterns(
        tenant_id="t1",
        intent_class="order",
        request_summary="đặt hàng",
        limit=4,
    )

    sql = db.fetch.call_args[0][0]
    assert "confidence DESC" in sql
    assert sql.index("confidence DESC") < sql.index("usage_count DESC")


# ---------------------------------------------------------------------------
# T100.2 — UPSERT ON CONFLICT uses GREATEST(confidence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_pattern_upsert_greatest_confidence():
    db = MagicMock()
    db.execute = AsyncMock()
    svc = OrchestratorMemoryService(db)

    await svc.store_pattern(
        tenant_id="t1",
        memory_type="routing_pattern",
        key="test_key",
        content="test_content",
        confidence=0.75,
    )

    sql = db.execute.call_args[0][0]
    assert "ON CONFLICT" in sql
    assert "GREATEST(" in sql
    # confidence value passed as positional arg
    assert 0.75 in db.execute.call_args[0]


# ---------------------------------------------------------------------------
# T100.3 — extract_and_store swallows all errors (best-effort)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_store_swallows_store_error():
    """Even if store_pattern raises, extract_and_store must not propagate."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("DB write failed"))
    svc = OrchestratorMemoryService(db)

    facts = [{"memory_type": "routing_pattern", "key": "k", "content": "c", "confidence": 0.8}]
    with patch.object(svc, "_call_extract_llm", new=AsyncMock(return_value=facts)):
        # Must not raise
        await svc.extract_and_store("t1", {"goal": "test"}, [])


# ---------------------------------------------------------------------------
# T100.4 — store_pattern with None db is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_pattern_none_db_noop():
    svc = OrchestratorMemoryService(None)
    await svc.store_pattern("t1", "routing_fix", "key", "content", 0.9)
    # No exception expected


# ---------------------------------------------------------------------------
# T100.5 — retrieve_patterns with None db returns empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_patterns_none_db_returns_empty():
    svc = OrchestratorMemoryService(None)
    assert await svc.retrieve_patterns("t1", "order", "test") == []
    assert await svc.find_similar_plans("t1", "test") == []


# ---------------------------------------------------------------------------
# T100.6 — store_pattern passes correct positional args ($1=tenant_id, $2=memory_type, $3=key, $4=content, $5=confidence)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_pattern_arg_order():
    db = MagicMock()
    db.execute = AsyncMock()
    svc = OrchestratorMemoryService(db)

    await svc.store_pattern("t1", "plan_template", "daily_revenue", "BI sub_goals only", 0.82)

    args = db.execute.call_args[0]
    assert args[1] == "t1"              # tenant_id
    assert args[2] == "plan_template"   # memory_type
    assert args[3] == "daily_revenue"   # key
    assert args[4] == "BI sub_goals only"  # content
    assert args[5] == 0.82             # confidence


# ---------------------------------------------------------------------------
# T100.7 — find_similar_plans filters status='completed' and user_message ILIKE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_similar_plans_sql_filters():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    svc = OrchestratorMemoryService(db)

    await svc.find_similar_plans(
        tenant_id="t1",
        user_message="doanh thu hôm nay",
        limit=3,
    )

    sql = db.fetch.call_args[0][0]
    assert "'completed'" in sql
    assert "ILIKE" in sql
    # limit passed
    args = db.fetch.call_args[0]
    assert 3 in args


# ---------------------------------------------------------------------------
# T100.8 — extract_and_store is no-op when db=None (no LLM call either)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_store_noop_when_no_db():
    svc = OrchestratorMemoryService(None)

    with patch.object(svc, "_call_extract_llm") as mock_llm:
        await svc.extract_and_store("t1", {"goal": "test"}, [])
        mock_llm.assert_not_called()

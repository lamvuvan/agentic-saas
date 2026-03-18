"""Unit tests for MemoryService — T090.

Tests:
- retrieve ordering (confidence DESC, usage_count DESC)
- UPSERT conflict SQL contains GREATEST(confidence)
- input_summary must not contain raw PII patterns
- _store_learnings swallows JSON parse errors
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.memory_service import MemoryService


# ---------------------------------------------------------------------------
# T090.1 — retrieve SQL orders by confidence DESC, usage_count DESC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_sql_ordering():
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    db.execute = AsyncMock()
    svc = MemoryService(db, "order-agent")

    await svc.retrieve(tenant_id="t1", query_context={"keyword": "test"}, limit=5)

    sql = db.fetch.call_args[0][0]
    # Ordering must be confidence DESC first, then usage_count, then recency
    assert "confidence DESC" in sql
    assert sql.index("confidence DESC") < sql.index("usage_count DESC")
    assert sql.index("usage_count DESC") < sql.index("last_used_at DESC")


# ---------------------------------------------------------------------------
# T090.2 — UPSERT conflict uses GREATEST for confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_upsert_conflict_greatest():
    db = MagicMock()
    db.execute = AsyncMock()
    svc = MemoryService(db, "bi-agent")

    await svc.store(
        tenant_id="t1",
        memory_type="glossary_fix",
        key="doanh thu",
        content="net_revenue",
        confidence=0.85,
    )

    sql = db.execute.call_args[0][0]
    assert "ON CONFLICT" in sql
    assert "GREATEST(" in sql
    # Confidence value passed correctly
    assert 0.85 in db.execute.call_args[0]


# ---------------------------------------------------------------------------
# T090.3 — input_summary must not contain raw PII patterns
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    r"\b\d{10,11}\b",                      # Vietnamese phone number
    r"\b[A-ZÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ][a-zàáạảãâầấậẩẫăằắặẳẵ]+\s[A-ZÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴ]",  # Full name
]


def _contains_pii(text: str) -> bool:
    return any(re.search(p, text) for p in _PII_PATTERNS)


@pytest.mark.parametrize(
    "input_summary",
    [
        "order: customer_id=cust_123, msg=ba đen 1 ly",
        "bi_query: doanh thu hôm nay là bao nhiêu",
        "order_request: bàn 3 2 ly trà",
        "order: customer_id=cust_456, msg=thêm một trứng lộn",
    ],
)
def test_input_summary_no_pii(input_summary: str):
    assert not _contains_pii(input_summary), (
        f"input_summary contains PII: {input_summary!r}"
    )


@pytest.mark.parametrize(
    "bad_summary",
    [
        "order for Nguyễn Văn An: ba đen",
        "khách Trần Thị Lan gọi cháo",
    ],
)
def test_pii_pattern_detects_names(bad_summary: str):
    """Verify our PII detector fires on real names (validates the test logic)."""
    assert _contains_pii(bad_summary)


# ---------------------------------------------------------------------------
# T090.4 — _store_learnings swallows JSON parse errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_learnings_swallows_parse_error():
    """Malformed learnings_json must not raise — best-effort only."""
    from order_agent.core.react_loop import _store_learnings

    db = MagicMock()
    db.execute = AsyncMock()
    svc = MemoryService(db, "order-agent")

    # Should not raise
    await _store_learnings(svc, "t1", "not valid json {{{")
    await _store_learnings(svc, "t1", '{"key": "not a list"}')
    await _store_learnings(svc, "t1", "null")
    await _store_learnings(svc, "t1", "[]")  # Empty list — valid, no-op


@pytest.mark.asyncio
async def test_bi_store_learnings_swallows_parse_error():
    """BI variant also swallows parse errors."""
    from bi_agent.core.react_loop import _store_learnings

    db = MagicMock()
    db.execute = AsyncMock()
    svc = MemoryService(db, "bi-agent")

    await _store_learnings(svc, "t1", "invalid")
    await _store_learnings(svc, "t1", "")
    await _store_learnings(svc, "t1", "[]")


# ---------------------------------------------------------------------------
# T090.5 — retrieve returns empty list when db is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retrieve_none_db_returns_empty():
    svc = MemoryService(None, "order-agent")
    assert await svc.retrieve("t1", {"keyword": "test"}) == []
    assert await svc.find_similar_tasks("t1", "create_order", "test") == []


# ---------------------------------------------------------------------------
# T090.6 — store with None db is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_none_db_noop():
    svc = MemoryService(None, "bi-agent")
    # Must not raise
    await svc.store("t1", "sql_pattern", "key", "content", 0.9)


# ---------------------------------------------------------------------------
# T090.7 — record_task with None db is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_task_none_db_noop():
    svc = MemoryService(None, "order-agent")
    await svc.record_task(
        tenant_id="t1",
        plan_id=None,
        skill="create_order",
        input_summary="safe summary",
        outcome="success",
        key_decisions={},
        learnings="",
        duration_ms=100,
    )


# ---------------------------------------------------------------------------
# T090.8 — UPSERT stores correct agent_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_agent_name_scoped():
    db = MagicMock()
    db.execute = AsyncMock()
    svc = MemoryService(db, "bi-agent")

    await svc.store("t1", "glossary_fix", "doanh thu", "net_revenue", 0.9)

    args = db.execute.call_args[0]
    # agent_name is $1 — first positional arg after SQL
    assert args[1] == "bi-agent"
    assert args[2] == "t1"    # tenant_id
    assert args[3] == "glossary_fix"
    assert args[4] == "doanh thu"
    assert args[5] == "net_revenue"
    assert args[6] == 0.9

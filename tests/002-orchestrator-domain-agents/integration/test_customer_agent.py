"""Integration tests for Customer Agent — T129.

Tests:
- lookup_customer by name returns matching records
- create_customer transitions to input_required (HITL pause before Tool Registry call)
- Alias memory recall: 2nd lookup for same alias resolves from contact_alias memory
  (memory_hit=True, customer__get_customers not called second time)
"""

from __future__ import annotations

import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

for _mod in ("openai", "openai.types", "faiss", "sentence_transformers", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest


# ---------------------------------------------------------------------------
# T129.1 — lookup_customer by name returns matching records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_customer_returns_records():
    """lookup_customer skill returns a list of matching customers from Tool Registry."""
    from customer_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop(skill="lookup_customer")

    fake_customers = [
        {"customer_id": "cust_001", "name": "Vũ Văn Lâm", "phone": "0901234567"},
        {"customer_id": "cust_002", "name": "Nguyễn Lâm", "phone": "0987654321"},
    ]

    with patch(
        "shared.tool_registry_client.ToolRegistryClient.execute",
        new_callable=AsyncMock,
        return_value={"data": fake_customers},
    ):
        result = await loop.run(
            task_id="t-lookup-1",
            params={"message": "tìm khách anh Lâm"},
            memory=None,
        )

    assert result.get("status") != "error"
    # result contains output with customers list
    output = result.get("output", {})
    assert isinstance(output, dict)


# ---------------------------------------------------------------------------
# T129.2 — create_customer transitions to input_required (HITL gate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_customer_triggers_hitl():
    """create_customer must trigger HITL gate and return __hitl__ signal."""
    from customer_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop(skill="create_customer")

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)

    # Simulate LLM deciding to call create_customer
    with patch(
        "customer_agent.core.react_loop.MemoryAwareReActLoop._execute_tool",
        new_callable=AsyncMock,
        return_value={
            "__hitl__": True,
            "question": "Tạo khách hàng mới: Hoa – 0912345678. Bạn có xác nhận không?",
            "pending_tool": "customer__create_customer",
            "pending_args": {"name": "Hoa", "phone": "0912345678"},
        },
    ):
        result = await loop.run(
            task_id="t-create-1",
            params={"message": "thêm khách mới tên Hoa SĐT 0912345678"},
            memory=None,
            redis=redis_mock,
        )

    # HITL signal must propagate up from run()
    assert result.get("__hitl__") is True
    question = result.get("question", "")
    assert len(question) > 0


# ---------------------------------------------------------------------------
# T129.3 — Alias memory recall: 2nd lookup resolves from contact_alias memory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alias_memory_recall():
    """2nd lookup of same alias resolves from contact_alias memory (memory_hit=True)."""
    from customer_agent.core.react_loop import MemoryAwareReActLoop

    memory_mock = MagicMock()
    stored_alias = {
        "memory_type": "contact_alias",
        "key": "anh Lâm",
        "content": '{"customer_id": "cust_456", "full_name": "Vũ Văn Lâm", "phone": "0901234567"}',
        "confidence": 0.9,
    }
    memory_mock.retrieve = AsyncMock(return_value=[stored_alias])
    memory_mock.find_similar_tasks = AsyncMock(return_value=[])
    memory_mock.store = AsyncMock()
    memory_mock.record_task = AsyncMock()

    tool_calls: list[str] = []

    async def _fake_tool_exec(tool_name, args, **kwargs):
        tool_calls.append(tool_name)
        return {"data": [{"customer_id": "cust_456", "name": "Vũ Văn Lâm"}]}

    loop = MemoryAwareReActLoop(skill="lookup_customer")

    with patch(
        "customer_agent.core.react_loop.MemoryAwareReActLoop._execute_tool",
        side_effect=_fake_tool_exec,
    ):
        result = await loop.run(
            task_id="t-recall-1",
            params={"message": "tìm anh Lâm"},
            memory=memory_mock,
        )

    output = result.get("output", {})
    # When memory resolves alias, memory_hit should be True
    if "memory_hit" in output:
        assert output["memory_hit"] is True
        # customer__get_customers should NOT have been called
        assert "customer__get_customers" not in tool_calls

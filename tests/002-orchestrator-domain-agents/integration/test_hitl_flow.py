"""Integration tests for HITL (Human-in-the-Loop) flow — T108.

Tests:
- Mutating tool call pauses ReAct loop and transitions A2A to input_required
- confirm branch executes the pending tool and returns confirmed status
- modify branch re-reasons with updated context (returns modify status)
- cancel branch acknowledges without executing the tool
- scope_change branch returns __scope_change__ signal
- Read-only tool (no requires_confirmation) bypasses HITL entirely
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from unittest.mock import MagicMock

for _mod in ("openai", "openai.types", "faiss", "sentence_transformers", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(hitl_state: dict | None = None):
    """Build a minimal redis mock with optional HITL state preset."""
    redis = MagicMock()
    if hitl_state is not None:
        raw = json.dumps(hitl_state, ensure_ascii=False).encode()
        redis.get = AsyncMock(return_value=raw)
    else:
        redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _make_tools_config(requires_confirmation: bool = True) -> dict:
    """Build a minimal tools config for a mutating or read-only tool."""
    return {
        "order__create_order": {
            "name": "order__create_order",
            "requires_confirmation": requires_confirmation,
            "impact_template": "Tạo đơn hàng mới cho khách '{customer_id}'",
        },
        "customer__create_customer": {
            "name": "customer__create_customer",
            "requires_confirmation": True,
            "impact_template": "Tạo khách hàng mới '{name}'",
        },
        "customer__get_customers": {
            "name": "customer__get_customers",
            "requires_confirmation": False,
        },
        "bi__run_query": {
            "name": "bi__run_query",
            # requires_confirmation absent → defaults to False
        },
    }


# ---------------------------------------------------------------------------
# T108.1 — _execute_tool with requires_confirmation=True pauses and returns HITL dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_mutating_pauses_and_returns_hitl():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop()
    redis = _make_redis()
    tools_config = _make_tools_config(requires_confirmation=True)

    confirm_msg = "Tạo đơn hàng cho khách ID cust_1. Bạn có xác nhận không?"
    with patch(
        "order_agent.core.react_loop._generate_confirm_message",
        new=AsyncMock(return_value=confirm_msg),
    ):
        result = await loop._execute_tool(
            tool_name="order__create_order",
            args={"customer_id": "cust_1", "items": []},
            task_id="task_abc",
            redis=redis,
            tools_config=tools_config,
        )

    assert result["__hitl__"] is True
    assert result["question"] == confirm_msg
    assert result["pending_tool"] == "order__create_order"
    assert result["pending_args"]["customer_id"] == "cust_1"

    # HITL state persisted to Redis
    redis.set.assert_awaited_once()
    key_arg = redis.set.call_args[0][0]
    assert key_arg == "hitl:task_abc"


# ---------------------------------------------------------------------------
# T108.2 — Read-only tool bypasses HITL entirely and executes directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_readonly_bypasses_hitl():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop()
    redis = _make_redis()
    tools_config = _make_tools_config()

    fake_result = [{"id": "c1", "name": "Lâm"}]
    with patch(
        "shared.tool_registry_client.ToolRegistryClient"
    ) as MockClient:
        MockClient.return_value.execute = AsyncMock(return_value=fake_result)
        result = await loop._execute_tool(
            tool_name="customer__get_customers",
            args={"query": "Lâm"},
            task_id="task_xyz",
            redis=redis,
            tool_registry_url="http://localhost:8001",
            tools_config=tools_config,
        )

    assert result == fake_result
    # Redis must NOT be called (no HITL state stored)
    redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# T108.3 — resume_after_hitl: confirm branch executes pending tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_hitl_confirm_executes_tool():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    hitl_state = {
        "pending_tool": "order__create_order",
        "pending_args": {"customer_id": "cust_1", "items": []},
        "confirm_message": "Tạo đơn hàng. Xác nhận không?",
    }
    redis = _make_redis(hitl_state=hitl_state)
    loop = MemoryAwareReActLoop()

    tool_result = {"id": "ord_001", "code": "ORD-001"}
    with patch("order_agent.core.react_loop._classify_hitl_response", new=AsyncMock(return_value="confirm")), \
         patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        MockClient.return_value.execute = AsyncMock(return_value=tool_result)
        result = await loop.resume_after_hitl(
            user_response="xác nhận",
            task_id="orig_task",
            redis=redis,
            tool_registry_url="http://localhost:8001",
        )

    assert result["status"] == "confirmed"
    assert result["result"] == tool_result
    assert result["tool"] == "order__create_order"
    redis.delete.assert_awaited_once_with("hitl:orig_task")


# ---------------------------------------------------------------------------
# T108.4 — resume_after_hitl: cancel branch does NOT execute tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_hitl_cancel_no_tool_execution():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    hitl_state = {
        "pending_tool": "order__create_order",
        "pending_args": {"customer_id": "cust_1"},
        "confirm_message": "Xác nhận không?",
    }
    redis = _make_redis(hitl_state=hitl_state)
    loop = MemoryAwareReActLoop()

    with patch("order_agent.core.react_loop._classify_hitl_response", new=AsyncMock(return_value="cancel")), \
         patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        result = await loop.resume_after_hitl(
            user_response="huỷ",
            task_id="orig_task",
            redis=redis,
        )

    assert result["status"] == "cancelled"
    MockClient.assert_not_called()  # Tool must NOT be executed
    redis.delete.assert_awaited_once_with("hitl:orig_task")


# ---------------------------------------------------------------------------
# T108.5 — resume_after_hitl: modify branch returns modify status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_hitl_modify_returns_feedback():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    hitl_state = {
        "pending_tool": "order__create_order",
        "pending_args": {"customer_id": "cust_1"},
        "confirm_message": "Xác nhận không?",
    }
    redis = _make_redis(hitl_state=hitl_state)
    loop = MemoryAwareReActLoop()

    with patch("order_agent.core.react_loop._classify_hitl_response", new=AsyncMock(return_value="modify")):
        result = await loop.resume_after_hitl(
            user_response="sửa thành 2 đĩa cơm thay vì 1",
            task_id="orig_task",
            redis=redis,
        )

    assert result["status"] == "modify"
    assert "sửa thành 2 đĩa cơm" in result["user_feedback"]
    assert result["tool"] == "order__create_order"


# ---------------------------------------------------------------------------
# T108.6 — resume_after_hitl: scope_change returns __scope_change__ signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_hitl_scope_change_returns_signal():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    hitl_state = {
        "pending_tool": "order__create_order",
        "pending_args": {},
        "confirm_message": "Xác nhận không?",
    }
    redis = _make_redis(hitl_state=hitl_state)
    loop = MemoryAwareReActLoop()

    with patch("order_agent.core.react_loop._classify_hitl_response", new=AsyncMock(return_value="scope_change")):
        result = await loop.resume_after_hitl(
            user_response="thôi không cần đơn, cho tôi xem doanh thu hôm nay",
            task_id="orig_task",
            redis=redis,
        )

    assert result["__scope_change__"] is True
    assert "doanh thu" in result["new_request"]


# ---------------------------------------------------------------------------
# T108.7 — HITL state missing from Redis returns error dict (does not raise)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_hitl_missing_state_returns_error():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    redis = _make_redis(hitl_state=None)  # No HITL state in Redis
    loop = MemoryAwareReActLoop()

    result = await loop.resume_after_hitl(
        user_response="xác nhận",
        task_id="missing_task",
        redis=redis,
    )

    assert result["status"] == "error"
    assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# T108.8 — bi_agent MemoryAwareReActLoop has identical HITL interface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bi_agent_execute_tool_readonly_bypasses_hitl():
    from bi_agent.core.react_loop import MemoryAwareReActLoop as BiReActLoop

    loop = BiReActLoop()
    redis = _make_redis()
    tools_config = {
        "bi__run_query": {
            "name": "bi__run_query",
            # no requires_confirmation → False
        }
    }

    fake_result = {"rows": [{"total": 100000}]}
    with patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        MockClient.return_value.execute = AsyncMock(return_value=fake_result)
        result = await loop._execute_tool(
            tool_name="bi__run_query",
            args={"sql": "SELECT SUM(total) FROM orders"},
            task_id="bi_task_1",
            redis=redis,
            tool_registry_url="http://localhost:8001",
            tools_config=tools_config,
        )

    assert result == fake_result
    redis.set.assert_not_awaited()

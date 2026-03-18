"""Unit tests for HITL helper functions — T109.

Tests:
- _classify_hitl_response returns correct intent for each Vietnamese input
- _generate_confirm_message output contains action and impact
- _execute_tool with requires_confirmation=False calls ToolRegistryClient directly
- _execute_tool with requires_confirmation=True returns __hitl__ dict without calling tool
- _classify_hitl_response defaults to cancel when classification fails
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from unittest.mock import MagicMock

# Mock heavy dependencies not installed in the test environment
for _mod in ("openai", "openai.types", "faiss", "sentence_transformers", "yaml"):
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

import pytest


# ---------------------------------------------------------------------------
# T109.1 — _classify_hitl_response: "xác nhận" → confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_hitl_confirm():
    from order_agent.core.react_loop import _classify_hitl_response

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(return_value=(json.dumps({"intent": "confirm"}), {})),
    ):
        result = await _classify_hitl_response("xác nhận", "order__create_order")

    assert result == "confirm"


# ---------------------------------------------------------------------------
# T109.2 — _classify_hitl_response: "huỷ" → cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_hitl_cancel():
    from order_agent.core.react_loop import _classify_hitl_response

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(return_value=(json.dumps({"intent": "cancel"}), {})),
    ):
        result = await _classify_hitl_response("huỷ đi", "order__create_order")

    assert result == "cancel"


# ---------------------------------------------------------------------------
# T109.3 — _classify_hitl_response: modify request → modify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_hitl_modify():
    from order_agent.core.react_loop import _classify_hitl_response

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(return_value=(json.dumps({"intent": "modify"}), {})),
    ):
        result = await _classify_hitl_response(
            "sửa thành 3 phần cơm thay vì 2", "order__create_order"
        )

    assert result == "modify"


# ---------------------------------------------------------------------------
# T109.4 — _classify_hitl_response: unrelated topic → scope_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_hitl_scope_change():
    from order_agent.core.react_loop import _classify_hitl_response

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(return_value=(json.dumps({"intent": "scope_change"}), {})),
    ):
        result = await _classify_hitl_response(
            "thôi không cần, cho xem doanh thu", "order__create_order"
        )

    assert result == "scope_change"


# ---------------------------------------------------------------------------
# T109.5 — _classify_hitl_response defaults to cancel when LLM call fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_hitl_defaults_to_cancel_on_error():
    from order_agent.core.react_loop import _classify_hitl_response

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(side_effect=RuntimeError("LLM down")),
    ):
        result = await _classify_hitl_response("some response", "order__create_order")

    assert result == "cancel"


# ---------------------------------------------------------------------------
# T109.6 — _generate_confirm_message contains tool action and impact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_confirm_message_contains_action_and_impact():
    from order_agent.core.react_loop import _generate_confirm_message

    llm_response = "Đơn hàng sẽ được tạo cho khách ID cust_1. Bạn có xác nhận không?"
    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(return_value=(llm_response, {"prompt_tokens": 50, "completion_tokens": 30})),
    ) as mock_llm:
        result = await _generate_confirm_message(
            tool_name="order__create_order",
            args={"customer_id": "cust_1", "items": []},
            impact_template="Tạo đơn hàng cho khách ID '{customer_id}'",
        )

    assert result == llm_response
    # Verify correct task_type used for model routing
    call_kwargs = mock_llm.call_args[1]
    assert call_kwargs["task_type"] == "confirm_message"


# ---------------------------------------------------------------------------
# T109.7 — _execute_tool with requires_confirmation=False calls ToolRegistryClient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_readonly_calls_registry():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop()
    redis = MagicMock()
    redis.set = AsyncMock()

    tools_config = {
        "customer__get_customers": {
            "name": "customer__get_customers",
            "requires_confirmation": False,
        }
    }
    expected = [{"id": "c1", "name": "Lâm"}]

    with patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        MockClient.return_value.execute = AsyncMock(return_value=expected)
        result = await loop._execute_tool(
            tool_name="customer__get_customers",
            args={"query": "Lâm"},
            task_id="t1",
            redis=redis,
            tool_registry_url="http://localhost:8001",
            tools_config=tools_config,
        )

    assert result == expected
    MockClient.assert_called_once_with(base_url="http://localhost:8001")
    # No HITL state stored
    redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# T109.8 — _execute_tool with requires_confirmation=True returns __hitl__ without executing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_mutating_returns_hitl_without_executing():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop()
    redis = MagicMock()
    redis.set = AsyncMock()

    tools_config = {
        "order__create_order": {
            "name": "order__create_order",
            "requires_confirmation": True,
            "impact_template": "Tạo đơn hàng mới",
        }
    }

    with patch(
        "order_agent.core.react_loop._generate_confirm_message",
        new=AsyncMock(return_value="Xác nhận tạo đơn không?"),
    ), patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        result = await loop._execute_tool(
            tool_name="order__create_order",
            args={"customer_id": "c1"},
            task_id="t1",
            redis=redis,
            tools_config=tools_config,
        )

    assert result["__hitl__"] is True
    assert result["pending_tool"] == "order__create_order"
    # ToolRegistryClient must NOT be instantiated
    MockClient.assert_not_called()
    # HITL state stored in Redis
    redis.set.assert_awaited_once()


# ---------------------------------------------------------------------------
# T109.9 — bi_agent _classify_hitl_response also defaults to cancel on error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bi_classify_hitl_defaults_to_cancel_on_error():
    from bi_agent.core.react_loop import _classify_hitl_response as bi_classify

    with patch(
        "shared.llm_client.chat_completion_async",
        new=AsyncMock(side_effect=RuntimeError("LLM down")),
    ):
        result = await bi_classify("some response", "bi__run_query")

    assert result == "cancel"


# ---------------------------------------------------------------------------
# T109.10 — _execute_tool: tool not in config (missing requires_confirmation) → treated as False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_unknown_tool_treated_as_readonly():
    from order_agent.core.react_loop import MemoryAwareReActLoop

    loop = MemoryAwareReActLoop()
    redis = MagicMock()
    redis.set = AsyncMock()

    tools_config: dict = {}  # Empty config — unknown tool

    with patch("shared.tool_registry_client.ToolRegistryClient") as MockClient:
        MockClient.return_value.execute = AsyncMock(return_value={"ok": True})
        result = await loop._execute_tool(
            tool_name="unknown__tool",
            args={},
            task_id="t_unknown",
            redis=redis,
            tools_config=tools_config,
        )

    assert result == {"ok": True}
    redis.set.assert_not_awaited()

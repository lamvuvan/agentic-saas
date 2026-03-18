"""Customer Agent LangGraph StateGraph (T073, T079).

Flow:
    START → retrieve_memory → classify_action
        ├── lookup   → lookup_customer → done → END
        ├── create   → prepare_create → [interrupt_before] hitl_confirm → create_customer → done → END
        └── update   → prepare_update → [interrupt_before] hitl_confirm → update_customer → done → END

Checkpointing: AsyncRedisSaver keyed by thread_id=task_id (TTL=1h).

HITL resume pattern (same as Order Agent):
    # First run: graph halts before hitl_confirm, confirm_message is set
    result = await compiled.ainvoke(initial_state, config)
    # → result["confirm_message"] set; A2A server sets status=input-required

    # Resume after user responds:
    from langgraph.types import Command
    result = await compiled.ainvoke(
        Command(resume={"user_confirmation": user_response}),
        config={"configurable": {"thread_id": task_id}}
    )
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from typing_extensions import TypedDict

from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_URL = os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001")

# ---------------------------------------------------------------------------
# State schema (T073)
# ---------------------------------------------------------------------------


class CustomerAgentState(TypedDict, total=False):
    """LangGraph state for Customer Agent (per data-model.md, T073).

    A2ATaskPayload fields + agent-specific processing fields.
    Serialized to/from Redis via AsyncRedisSaver (thread_id=task_id, TTL=1h).
    """

    # A2ATaskPayload identity fields
    task_id: str
    plan_id: str
    original_message: str
    instructions: str
    conversation_history: list
    dependency_results: dict

    # Agent-specific processing fields
    memory_context: str
    action: str  # "lookup" | "create" | "update"
    customer_data: dict  # extracted name, phone, address
    confirm_message: str  # presented to user before interrupt
    user_confirmation: str  # populated by Command(resume={"user_confirmation": ...})
    result: dict  # final output


# ---------------------------------------------------------------------------
# Module-level compiled graph — initialized at lifespan
# ---------------------------------------------------------------------------

_compiled = None


async def setup_checkpointer(redis_url: str | None = None) -> None:
    """Initialize compiled StateGraph with AsyncRedisSaver."""
    global _compiled  # noqa: PLW0603
    url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: PLC0415

        saver = AsyncRedisSaver(redis_url=url)
        await saver.asetup()
        _compiled = _build_graph().compile(
            checkpointer=saver,
            interrupt_before=["hitl_confirm"],
        )
        logger.info("customer_graph_ready", extra={"redis_url": url.split("@")[-1]})
    except Exception as exc:  # pragma: no cover
        logger.warning("customer_graph_unavailable", extra={"error": str(exc)})
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        _compiled = _build_graph().compile(
            checkpointer=MemorySaver(),
            interrupt_before=["hitl_confirm"],
        )


# ---------------------------------------------------------------------------
# Node wrapper functions
# ---------------------------------------------------------------------------


async def _retrieve_memory_node(state: CustomerAgentState, config: dict) -> dict:
    """Retrieve contact_alias and customer_profile memories."""
    memory = config.get("configurable", {}).get("memory")
    if memory is None:
        return {"memory_context": ""}
    try:
        facts = await memory.retrieve(
            tenant_id=config.get("configurable", {}).get("tenant_id", "default"),
            query_context=state.get("original_message", "")[:50],
            limit=5,
        )
        ctx_parts = []
        for f in facts:
            content = f.get("content", "")
            if content:
                ctx_parts.append(content)
        return {"memory_context": "\n".join(ctx_parts)}
    except Exception:
        return {"memory_context": ""}


async def _classify_action_node(state: CustomerAgentState, config: dict) -> dict:
    """Classify customer action and extract customer_data via GPT-4o-mini."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    text = f"{state.get('instructions', '')}\n{state.get('original_message', '')}".strip()
    memory_context = state.get("memory_context", "")

    system = (
        "Phân loại yêu cầu quản lý khách hàng và trích xuất thông tin.\n"
        "Trả về JSON:\n"
        '{"action": "lookup"|"create"|"update", "customer_data": {"name": str|null, "phone": str|null, "address": str|null}}'
    )
    if memory_context:
        system += f"\n\nKiến thức tích lũy:\n{memory_context}"

    schema = {
        "name": "customer_action",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["lookup", "create", "update"]},
                "customer_data": {
                    "type": "object",
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "phone": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                    },
                    "required": ["name", "phone", "address"],
                    "additionalProperties": False,
                },
            },
            "required": ["action", "customer_data"],
            "additionalProperties": False,
        },
    }

    try:
        raw = await chat_completion_async(
            messages=[{"role": "user", "content": text}],
            system=system,
            task_type="entity_extract",
            response_schema=schema,
        )
        data = json.loads(raw)
        return {
            "action": data.get("action", "lookup"),
            "customer_data": data.get("customer_data", {}),
        }
    except Exception as exc:
        logger.warning("classify_action_failed", extra={"error": str(exc)})
        return {"action": "lookup", "customer_data": {}}


async def _lookup_customer_node(state: CustomerAgentState, config: dict) -> dict:
    """Look up customer — check contact_alias memory first, then Tool Registry."""
    from shared.tool_registry_client import ToolRegistryClient  # noqa: PLC0415

    memory = config.get("configurable", {}).get("memory")
    tool_registry_url = config.get("configurable", {}).get("tool_registry_url", _TOOL_REGISTRY_URL)
    tenant_id = config.get("configurable", {}).get("tenant_id", "default")

    customer_data = state.get("customer_data", {})
    name = customer_data.get("name", "")

    # Check contact_alias memory first
    if memory is not None and name:
        try:
            facts = await memory.retrieve(
                tenant_id=tenant_id,
                query_context=name,
                limit=3,
            )
            for f in facts:
                if f.get("memory_type") == "contact_alias" and name.lower() in f.get("key", "").lower():
                    content = f.get("content", "")
                    if content:
                        try:
                            cached = json.loads(content)
                            return {
                                "result": {
                                    "status": "success",
                                    "customers": [cached],
                                    "memory_hit": True,
                                    "message": f"Tìm thấy khách hàng từ bộ nhớ: {cached.get('full_name', name)}",
                                }
                            }
                        except Exception:
                            pass
        except Exception:
            pass

    # Call Tool Registry
    client = ToolRegistryClient(base_url=tool_registry_url)
    try:
        result = await client.execute("customer__get_customers", {"name": name, "phone": customer_data.get("phone")})
        customers = result.get("customers", result.get("data", []))

        # Cache first result as contact_alias for future lookups
        if memory is not None and customers and name:
            first = customers[0]
            try:
                await memory.store(
                    tenant_id=tenant_id,
                    memory_type="contact_alias",
                    key=name.lower(),
                    content=json.dumps({
                        "customer_id": first.get("id", first.get("customer_id", "")),
                        "full_name": first.get("name", first.get("full_name", "")),
                        "phone": first.get("phone", ""),
                    }),
                    confidence=0.9,
                )
            except Exception:
                pass

        return {
            "result": {
                "status": "success",
                "customers": customers,
                "memory_hit": False,
                "message": f"Tìm thấy {len(customers)} khách hàng.",
            }
        }
    except Exception as exc:
        return {
            "result": {
                "status": "error",
                "message": f"Không tìm thấy khách hàng: {exc}",
                "customers": [],
            }
        }


async def _prepare_create_node(state: CustomerAgentState, config: dict) -> dict:
    """Prepare create_customer confirmation message."""
    customer_data = state.get("customer_data", {})
    name = customer_data.get("name", "Khách mới")
    phone = customer_data.get("phone", "")
    address = customer_data.get("address", "")

    msg = f"Tạo khách hàng mới: {name}"
    if phone:
        msg += f" – {phone}"
    if address:
        msg += f" – {address}"
    msg += ".\nBạn có xác nhận không?"

    return {"confirm_message": msg}


async def _prepare_update_node(state: CustomerAgentState, config: dict) -> dict:
    """Prepare update_customer confirmation message."""
    customer_data = state.get("customer_data", {})
    name = customer_data.get("name", "Khách hàng")
    phone = customer_data.get("phone", "")
    address = customer_data.get("address", "")

    parts = []
    if phone:
        parts.append(f"SĐT: {phone}")
    if address:
        parts.append(f"địa chỉ: {address}")

    changes = ", ".join(parts) if parts else "thông tin"
    msg = f"Cập nhật {changes} cho khách hàng {name}.\nBạn có xác nhận không?"

    return {"confirm_message": msg}


async def _hitl_confirm_node(state: CustomerAgentState, config: dict) -> dict:
    """HITL confirm — runs after interrupt fires, classifies user_confirmation."""
    from shared.llm_client import chat_completion_async  # noqa: PLC0415

    user_input = state.get("user_confirmation", "")
    if not user_input:
        return {"result": {"status": "cancelled", "message": "Thao tác đã huỷ.", "__hitl_intent__": "cancel"}}

    system = (
        "Phân loại phản hồi người dùng sau khi được hỏi xác nhận thao tác khách hàng:\n"
        "- confirm: đồng ý (xác nhận, ok, được, yes, đúng)\n"
        "- cancel: từ chối (huỷ, không, thôi, cancel)\n"
        "- modify: muốn sửa đổi thông tin\n"
        "- scope_change: đổi hoàn toàn sang chủ đề khác\n"
        "Khi không chắc → cancel.\n"
        'Trả về JSON: {"intent": "confirm"|"cancel"|"modify"|"scope_change"}'
    )

    schema = {
        "name": "hitl_classification",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["confirm", "cancel", "modify", "scope_change"]}
            },
            "required": ["intent"],
            "additionalProperties": False,
        },
    }

    try:
        raw = await chat_completion_async(
            messages=[{"role": "user", "content": user_input}],
            system=system,
            task_type="entity_extract",
            response_schema=schema,
        )
        intent = json.loads(raw).get("intent", "cancel")
    except Exception:
        # Keyword fallback
        text = user_input.lower()
        if any(w in text for w in {"xác nhận", "ok", "được", "yes", "có"}):
            intent = "confirm"
        else:
            intent = "cancel"

    if intent == "cancel":
        return {"result": {"status": "cancelled", "message": "Thao tác đã huỷ.", "__hitl_intent__": "cancel"}}
    if intent == "scope_change":
        return {"result": {"__scope_change__": True, "new_request": user_input, "__hitl_intent__": "scope_change"}}
    if intent == "modify":
        return {
            "original_message": user_input,
            "customer_data": {},
            "user_confirmation": "",
            "confirm_message": "",
            "result": {"__hitl_intent__": "modify"},
        }

    return {"result": {"__hitl_intent__": "confirm"}}


async def _create_customer_node(state: CustomerAgentState, config: dict) -> dict:
    """Create customer via Tool Registry."""
    if state.get("result", {}).get("__hitl_intent__") != "confirm":
        return {}

    from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError  # noqa: PLC0415

    tool_registry_url = config.get("configurable", {}).get("tool_registry_url", _TOOL_REGISTRY_URL)
    customer_data = state.get("customer_data", {})
    client = ToolRegistryClient(base_url=tool_registry_url)

    try:
        result = await client.execute("customer__create_customer", {
            "name": customer_data.get("name", ""),
            "phone": customer_data.get("phone", ""),
            "address": customer_data.get("address", ""),
        })
        return {
            "result": {
                "status": "success",
                "message": f"Đã tạo khách hàng: {customer_data.get('name', '')}",
                "customer": result,
                "__hitl_intent__": "confirm",
            }
        }
    except ToolRegistryError as exc:
        return {"result": {"status": "error", "message": str(exc)}}


async def _update_customer_node(state: CustomerAgentState, config: dict) -> dict:
    """Update customer via Tool Registry."""
    if state.get("result", {}).get("__hitl_intent__") != "confirm":
        return {}

    from shared.tool_registry_client import ToolRegistryClient, ToolRegistryError  # noqa: PLC0415

    tool_registry_url = config.get("configurable", {}).get("tool_registry_url", _TOOL_REGISTRY_URL)
    customer_data = state.get("customer_data", {})
    client = ToolRegistryClient(base_url=tool_registry_url)

    try:
        result = await client.execute("customer__update_customer", {
            "name": customer_data.get("name", ""),
            "phone": customer_data.get("phone", ""),
            "address": customer_data.get("address", ""),
        })
        return {
            "result": {
                "status": "success",
                "message": f"Đã cập nhật khách hàng: {customer_data.get('name', '')}",
                "customer": result,
                "__hitl_intent__": "confirm",
            }
        }
    except ToolRegistryError as exc:
        return {"result": {"status": "error", "message": str(exc)}}


async def _done_node(state: CustomerAgentState, config: dict) -> dict:
    """Record task history and finalize."""
    memory = config.get("configurable", {}).get("memory")
    if memory is None:
        return {}

    result = state.get("result", {})
    outcome = "success" if result.get("status") == "success" else "failure"

    try:
        await memory.record_task(
            tenant_id=config.get("configurable", {}).get("tenant_id", "default"),
            plan_id=state.get("plan_id", ""),
            skill=f"customer_{state.get('action', 'lookup')}",
            input_summary=state.get("original_message", "")[:100],
            outcome=outcome,
            key_decisions=[f"action={state.get('action', '')}"],
            learnings=[],
            duration_ms=0,
        )
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def _route_after_classify(state: CustomerAgentState) -> str:
    action = state.get("action", "lookup")
    if action == "create":
        return "prepare_create"
    if action == "update":
        return "prepare_update"
    return "lookup_customer"


def _route_after_confirm(state: CustomerAgentState) -> str:
    result = state.get("result", {})
    intent = result.get("__hitl_intent__", "cancel")
    action = state.get("action", "lookup")

    if intent == "confirm":
        if action == "create":
            return "create_customer"
        return "update_customer"
    if intent == "modify":
        return "classify_action"
    return "done"


# ---------------------------------------------------------------------------
# StateGraph assembly (T079)
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    g = StateGraph(CustomerAgentState)

    g.add_node("retrieve_memory", _retrieve_memory_node)
    g.add_node("classify_action", _classify_action_node)
    g.add_node("lookup_customer", _lookup_customer_node)
    g.add_node("prepare_create", _prepare_create_node)
    g.add_node("prepare_update", _prepare_update_node)
    g.add_node("hitl_confirm", _hitl_confirm_node)
    g.add_node("create_customer", _create_customer_node)
    g.add_node("update_customer", _update_customer_node)
    g.add_node("done", _done_node)

    g.set_entry_point("retrieve_memory")
    g.add_edge("retrieve_memory", "classify_action")
    g.add_conditional_edges(
        "classify_action",
        _route_after_classify,
        {
            "lookup_customer": "lookup_customer",
            "prepare_create": "prepare_create",
            "prepare_update": "prepare_update",
        },
    )

    # Lookup path: no HITL
    g.add_edge("lookup_customer", "done")

    # Create/update paths: both go through HITL
    g.add_edge("prepare_create", "hitl_confirm")
    g.add_edge("prepare_update", "hitl_confirm")
    g.add_conditional_edges(
        "hitl_confirm",
        _route_after_confirm,
        {
            "create_customer": "create_customer",
            "update_customer": "update_customer",
            "classify_action": "classify_action",
            "done": "done",
        },
    )
    g.add_edge("create_customer", "done")
    g.add_edge("update_customer", "done")
    g.add_edge("done", END)

    return g


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_customer_graph(
    task_id: str,
    params: dict[str, Any],
    memory: Any = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Execute Customer Agent pipeline via StateGraph.

    Returns dict with status and output suitable for A2A response.
    """
    graph = _compiled
    if graph is None:
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        graph = _build_graph().compile(
            checkpointer=MemorySaver(),
            interrupt_before=["hitl_confirm"],
        )

    initial_state: CustomerAgentState = {
        "task_id": task_id,
        "plan_id": params.get("plan_id", ""),
        "original_message": params.get("message", params.get("original_message", "")),
        "instructions": params.get("instructions", ""),
        "conversation_history": params.get("conversation_history", []),
        "dependency_results": params.get("dependency_results", {}),
        "memory_context": "",
        "action": "",
        "customer_data": {},
        "confirm_message": "",
        "user_confirmation": "",
        "result": {},
    }

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": task_id,
            "memory": memory,
            "tenant_id": tenant_id,
            "tool_registry_url": os.environ.get("TOOL_REGISTRY_URL", _TOOL_REGISTRY_URL),
        }
    }

    result_state = await graph.ainvoke(initial_state, config)

    # Interrupted at hitl_confirm: confirm_message set, user_confirmation empty
    if result_state.get("confirm_message") and not result_state.get("user_confirmation"):
        return {
            "status": "input-required",
            "input_request": result_state["confirm_message"],
            "reasoning_summary": f"Customer {result_state.get('action', 'action')} awaiting confirmation.",
            "tool_calls": [],
        }

    final_result = result_state.get("result", {})
    return {
        "output": {k: v for k, v in final_result.items() if not k.startswith("__")},
        "reasoning_summary": final_result.get("message", "Customer task completed."),
        "tool_calls": [
            f"customer__{result_state.get('action', 'lookup')}"
        ] if final_result.get("status") == "success" else [],
        "status": final_result.get("status", "completed"),
    }

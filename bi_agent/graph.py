"""BI Agent LangGraph StateGraph (T051, T057).

Flow: START → retrieve_memory → nl2sql → safety_check → execute_query → format_result → done → END

No HITL interrupt — BI queries are stateless per task (SELECT-only, fast < 5s).
Checkpointing: MemorySaver (in-process, per-task). Pod restart mid-task is
acceptable since BI has no pending confirmation state to preserve.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from typing_extensions import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from shared.a2a.trace import make_traced_node

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_URL = os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001")


# ---------------------------------------------------------------------------
# State schema (T051)
# ---------------------------------------------------------------------------


class BIAgentState(TypedDict, total=False):
    """LangGraph state for BI Agent (per data-model.md, T051).

    No HITL fields — BI Agent is stateless and SELECT-only.
    Uses MemorySaver (in-process) instead of AsyncRedisSaver.
    """

    # A2ATaskPayload fields
    task_id: str
    plan_id: str
    original_message: str
    instructions: str
    conversation_history: list
    dependency_results: dict

    # Agent-specific processing fields
    memory_context: str
    generated_sql: str
    query_result: list
    result: dict  # final output


# ---------------------------------------------------------------------------
# Node wrapper functions
# ---------------------------------------------------------------------------


async def _retrieve_memory_node(state: BIAgentState, config: dict) -> dict:
    """Retrieve relevant SQL patterns / glossary fixes from MemoryService."""
    memory = config.get("configurable", {}).get("memory")
    if memory is None:
        return {"memory_context": ""}
    try:
        facts = await memory.retrieve(
            tenant_id=config.get("configurable", {}).get("tenant_id", "default"),
            query_context=state.get("original_message", "")[:50],
            limit=5,
        )
        ctx = "\n".join(f.get("content", "") for f in facts if f.get("content"))
        return {"memory_context": ctx}
    except Exception:
        return {"memory_context": ""}


async def _nl2sql_node(state: BIAgentState, config: dict) -> dict:
    """Translate Vietnamese natural language to SQL via GPT-4o."""
    from bi_agent.nodes.schema_explorer import load_schema_context  # noqa: PLC0415
    from bi_agent.nodes.nl2sql import generate_sql  # noqa: PLC0415

    schema_context = load_schema_context()
    nl_input = state.get("original_message", "")
    task_id = state.get("task_id", "")

    sql, explanation = await generate_sql(
        nl_input=nl_input,
        schema_context=schema_context,
        trace_id=task_id,
    )

    if not sql:
        return {
            "result": {
                "status": "error",
                "message": "Không thể tạo câu truy vấn. Vui lòng thử lại với câu hỏi khác.",
                "error_reason": explanation,
            }
        }

    return {"generated_sql": sql}


async def _safety_check_node(state: BIAgentState, config: dict) -> dict:
    """Enforce SELECT-only and inject LIMIT 500."""
    # Short-circuit if upstream already failed
    if state.get("result", {}).get("status") == "error":
        return {}

    from bi_agent.nodes.safety_check import check_safety, inject_limit  # noqa: PLC0415

    sql = state.get("generated_sql", "")
    passed, reason = check_safety(sql)
    if not passed:
        return {
            "result": {
                "status": "rejected",
                "message": "Tôi chỉ có thể truy vấn dữ liệu, không thể sửa đổi. Vui lòng thử lại.",
                "error_reason": reason or "Non-SELECT SQL detected",
            }
        }

    safe_sql = inject_limit(sql, max_rows=500)
    return {"generated_sql": safe_sql}


async def _execute_query_node(state: BIAgentState, config: dict) -> dict:
    """Execute the safe SQL via Tool Registry."""
    if state.get("result", {}).get("status") in ("rejected", "error"):
        return {}

    from bi_agent.nodes.execute_query import execute_query  # noqa: PLC0415

    try:
        rows, _ = await execute_query(state.get("generated_sql", ""), _TOOL_REGISTRY_URL)
        return {"query_result": rows}
    except Exception as exc:
        logger.error("bi_execute_failed", extra={"error": str(exc), "task_id": state.get("task_id", "")})
        return {
            "result": {
                "status": "error",
                "message": "Có lỗi khi thực hiện truy vấn. Vui lòng thử lại.",
                "error_reason": str(exc),
            }
        }


async def _format_result_node(state: BIAgentState, config: dict) -> dict:
    """Format query result as Vietnamese human-readable answer."""
    if state.get("result", {}).get("status") in ("rejected", "error"):
        return {}

    from bi_agent.nodes.format_response import format_response  # noqa: PLC0415

    rows = state.get("query_result", [])
    sql = state.get("generated_sql", "")
    nl_input = state.get("original_message", "")

    formatted = await format_response(
        nl_input=nl_input,
        rows=rows,
        row_count=len(rows),
        generated_sql=sql,
        trace_id=state.get("task_id", ""),
    )

    return {
        "result": {
            "status": "success",
            "message": formatted,
            "generated_sql": sql,
            "row_count": len(rows),
            "limit_applied": "LIMIT" in sql.upper(),
            "data": rows[:500],
        }
    }


async def _done_node(state: BIAgentState, config: dict) -> dict:
    """Record task history and extract learnings (best-effort)."""
    memory = config.get("configurable", {}).get("memory")
    if memory is None:
        return {}

    try:
        result = state.get("result", {})
        outcome = "success" if result.get("status") == "success" else "failure"
        await memory.record_task(
            tenant_id=config.get("configurable", {}).get("tenant_id", "default"),
            plan_id=state.get("plan_id", ""),
            skill="bi_query",
            input_summary=state.get("original_message", "")[:100],
            outcome=outcome,
            key_decisions=[f"SQL: {state.get('generated_sql', '')[:100]}"],
            learnings=[],
            duration_ms=0,
        )
    except Exception:
        pass
    return {}


# ---------------------------------------------------------------------------
# Trace summary extractor
# ---------------------------------------------------------------------------


def _bi_summary(node_name: str, result: dict) -> dict:
    """Extract a lightweight summary for a completed BI node."""
    if node_name == "retrieve_memory":
        return {"has_context": bool(result.get("memory_context"))}
    if node_name == "nl2sql":
        sql = result.get("generated_sql", "")
        failed = result.get("result", {}).get("status") == "error"
        return {"sql_preview": sql[:120] if sql else "", "failed": failed}
    if node_name == "safety_check":
        rejected = result.get("result", {}).get("status") == "rejected"
        return {"passed": not rejected}
    if node_name == "execute_query":
        return {"row_count": len(result.get("query_result", []))}
    if node_name == "format_result":
        inner = result.get("result", {})
        return {"status": inner.get("status"), "row_count": inner.get("row_count", 0)}
    if node_name == "done":
        return {}
    return {}


# ---------------------------------------------------------------------------
# StateGraph assembly (T057)
# ---------------------------------------------------------------------------


def _build_graph() -> StateGraph:
    g = StateGraph(BIAgentState)

    _t = lambda name, fn: make_traced_node(name, fn, _bi_summary)  # noqa: E731
    g.add_node("retrieve_memory", _t("retrieve_memory", _retrieve_memory_node))
    g.add_node("nl2sql",          _t("nl2sql",          _nl2sql_node))
    g.add_node("safety_check",    _t("safety_check",    _safety_check_node))
    g.add_node("execute_query",   _t("execute_query",   _execute_query_node))
    g.add_node("format_result",   _t("format_result",   _format_result_node))
    g.add_node("done",            _t("done",            _done_node))

    g.set_entry_point("retrieve_memory")
    g.add_edge("retrieve_memory", "nl2sql")
    g.add_edge("nl2sql", "safety_check")
    g.add_edge("safety_check", "execute_query")
    g.add_edge("execute_query", "format_result")
    g.add_edge("format_result", "done")
    g.add_edge("done", END)

    return g


# Compiled with MemorySaver (in-process, per-task — no Redis needed for BI)
_compiled = _build_graph().compile(checkpointer=MemorySaver())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_bi_graph(
    task_id: str,
    params: dict[str, Any],
    memory: Any = None,
    tenant_id: str = "default",
    redis: Any = None,
) -> dict[str, Any]:
    """Execute BI Agent pipeline via StateGraph.

    Returns A2AResult-shaped dict.
    """
    initial_state: BIAgentState = {
        "task_id": task_id,
        "plan_id": params.get("plan_id", ""),
        "original_message": params.get("message", ""),
        "instructions": params.get("instructions", ""),
        "conversation_history": params.get("conversation_history", []),
        "dependency_results": params.get("dependency_results", {}),
        "memory_context": "",
        "generated_sql": "",
        "query_result": [],
        "result": {},
    }

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": task_id,
            "memory": memory,
            "tenant_id": tenant_id,
            "redis": redis,
        }
    }

    result_state = await _compiled.ainvoke(initial_state, config)
    final_result = result_state.get("result", {})
    sql = result_state.get("generated_sql", "")
    status = final_result.get("status", "error")

    return {
        "output": final_result,
        "reasoning_summary": (
            f"Generated SQL: {sql[:80]}. Returned {final_result.get('row_count', 0)} rows."
            if status == "success"
            else f"BI query {status}: {final_result.get('error_reason', final_result.get('message', ''))[:80]}"
        ),
        "tool_calls": ["bi__run_query"] if status == "success" else [],
    }

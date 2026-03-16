"""BI Agent pipeline.

Flow: schema_explorer → generate_sql → safety_check → execute_query → format_response
Short-circuit: safety_check failure → return rejected result immediately
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_TOOL_REGISTRY_URL = os.environ.get("TOOL_REGISTRY_URL", "http://tool-registry:8001")


async def run_bi_graph(
    task_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """
    Execute the BI Agent pipeline for a task.

    Returns dict with:
    - output: BIQueryResult-shaped dict
    - reasoning_summary: str
    - tool_calls: list[str]
    """
    from bi_agent.nodes.schema_explorer import load_schema_context  # noqa: PLC0415
    from bi_agent.nodes.nl2sql import generate_sql  # noqa: PLC0415
    from bi_agent.nodes.safety_check import check_safety, inject_limit  # noqa: PLC0415
    from bi_agent.nodes.execute_query import execute_query  # noqa: PLC0415
    from bi_agent.nodes.format_response import format_response  # noqa: PLC0415

    nl_input = params.get("message", "")
    tool_calls: list[str] = []

    # Step 1: Load schema context
    schema_context = load_schema_context()

    # Step 2: Generate SQL
    sql, explanation = await generate_sql(
        nl_input=nl_input,
        schema_context=schema_context,
        trace_id=task_id,
    )

    if not sql:
        return {
            "output": {
                "status": "error",
                "message": "Không thể tạo câu truy vấn. Vui lòng thử lại với câu hỏi khác.",
                "error_reason": explanation,
            },
            "reasoning_summary": f"SQL generation failed: {explanation}",
            "tool_calls": [],
        }

    # Step 3: Safety check
    passed, reason = check_safety(sql)
    if not passed:
        return {
            "output": {
                "status": "rejected",
                "message": "Tôi chỉ có thể truy vấn dữ liệu, không thể sửa đổi. Vui lòng thử lại với câu hỏi khác.",
                "error_reason": reason or "Non-SELECT SQL detected",
            },
            "reasoning_summary": f"Query rejected by safety check: {reason}",
            "tool_calls": [],
        }

    # Step 4: Inject LIMIT if needed
    sql_with_limit = inject_limit(sql, max_rows=500)
    limit_applied = sql_with_limit != sql

    # Step 5: Execute query via Tool Registry
    tool_calls.append("bi__run_query")
    try:
        rows, _ = await execute_query(sql_with_limit, _TOOL_REGISTRY_URL)
        row_count = len(rows)
    except Exception as exc:
        logger.error("bi_execute_failed", extra={"error": str(exc), "task_id": task_id})
        return {
            "output": {
                "status": "error",
                "message": "Có lỗi khi thực hiện truy vấn. Vui lòng thử lại.",
                "error_reason": str(exc),
            },
            "reasoning_summary": f"Query execution failed: {exc}",
            "tool_calls": tool_calls,
        }

    # Step 6: Format response
    formatted = await format_response(
        nl_input=nl_input,
        rows=rows,
        row_count=row_count,
        generated_sql=sql_with_limit,
        trace_id=task_id,
    )

    return {
        "output": {
            "status": "success",
            "message": formatted,
            "generated_sql": sql_with_limit,
            "row_count": row_count,
            "limit_applied": limit_applied,
            "data": rows[:500],
        },
        "reasoning_summary": f"Generated SQL: {explanation}. Returned {row_count} rows.",
        "tool_calls": tool_calls,
    }

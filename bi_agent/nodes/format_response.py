"""Format response node — GPT-4o-mini formats raw SQL results as Vietnamese text."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "format_response_v1.md"


async def format_response(
    nl_input: str,
    rows: list[dict],
    row_count: int,
    generated_sql: str,
    trace_id: str = "",
) -> str:
    """
    Format SQL query results into a human-readable Vietnamese response.

    Returns the formatted text string.
    """
    if row_count == 0:
        return "Không có dữ liệu phù hợp với yêu cầu của bạn."

    # Serialize up to first 20 rows for context (avoid token explosion)
    sample_rows = rows[:20]
    rows_text = json.dumps(sample_rows, ensure_ascii=False, indent=2)
    if row_count > 20:
        rows_text += f"\n... và {row_count - 20} kết quả khác"

    system_text = _PROMPT_PATH.read_text(encoding="utf-8")

    messages = [
        {"role": "system", "content": system_text},
        {
            "role": "user",
            "content": (
                f"Câu hỏi: {nl_input}\n"
                f"Số kết quả: {row_count}\n"
                f"Dữ liệu:\n{rows_text}"
            ),
        },
    ]

    content, _ = await chat_completion_async(
        messages=messages,
        task_type="response_format",
        extra_log={"trace_id": trace_id, "row_count": row_count},
    )
    return content.strip()

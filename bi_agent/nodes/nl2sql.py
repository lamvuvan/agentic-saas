"""NL2SQL node — GPT-4o translates Vietnamese questions to SQL."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "nl2sql_v1.md"

_SQL_SCHEMA = {
    "name": "sql_generation",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": ["sql", "explanation"],
        "additionalProperties": False,
    },
}


async def generate_sql(
    nl_input: str,
    schema_context: str,
    trace_id: str = "",
) -> tuple[str, str]:
    """
    Generate SQL from Vietnamese natural language input.

    Returns:
        (sql_query, explanation)
    """
    prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
    prompt_with_schema = prompt_template.replace("{{SCHEMA_CONTEXT}}", schema_context)

    system_text = prompt_with_schema.split("## Few-Shot Examples")[0].strip()
    messages = [{"role": "system", "content": system_text}]

    # Parse few-shot examples
    examples_section = prompt_with_schema.split("## Few-Shot Examples")[-1].strip()
    for line in examples_section.split("\n"):
        line = line.strip()
        if line.startswith("User:"):
            messages.append({"role": "user", "content": line[5:].strip()})
        elif line.startswith("Assistant:"):
            messages.append({"role": "assistant", "content": line[10:].strip()})

    messages.append({"role": "user", "content": nl_input})

    content, _ = await chat_completion_async(
        messages=messages,
        task_type="nl2sql",
        json_schema=_SQL_SCHEMA,
        extra_log={"trace_id": trace_id},
    )

    try:
        result = json.loads(content)
        return result["sql"].strip(), result.get("explanation", "")
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("nl2sql_parse_error", extra={"raw": content[:200], "error": str(exc)})
        return "", f"SQL generation failed: {exc}"

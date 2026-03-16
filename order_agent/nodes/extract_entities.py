"""Entity extraction node — GPT-4o-mini extracts OrderEntities from Vietnamese input."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from order_agent.models import OrderEntities
from order_agent.vn_utils import normalize_text, words_to_numbers
from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "entity_extract_v1.md"
_PROMPT_CACHE: str | None = None

_ENTITIES_SCHEMA = {
    "name": "order_entities",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "customer_name": {"type": ["string", "null"]},
            "customer_honorific": {"type": ["string", "null"]},
            "table_number": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_query": {"type": "string"},
                        "quantity": {"type": "integer"},
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["product_query", "quantity", "note"],
                    "additionalProperties": False,
                },
            },
            "notes": {"type": ["string", "null"]},
            "intent_modifier": {
                "type": "string",
                "enum": ["new", "add", "remove", "cancel"],
            },
        },
        "required": [
            "customer_name", "customer_honorific", "table_number",
            "items", "notes", "intent_modifier",
        ],
        "additionalProperties": False,
    },
}


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


async def extract_entities(message: str, trace_id: str = "") -> OrderEntities:
    """
    Extract structured order entities from a Vietnamese natural language message.
    Pre-processes: NFC normalization, number word conversion.
    """
    normalized = normalize_text(message)
    preprocessed = words_to_numbers(normalized)

    prompt = _load_prompt()
    system_text = prompt.split("## Few-Shot Examples")[0].strip()
    messages = [{"role": "system", "content": system_text}]

    # Parse few-shot examples
    examples_section = prompt.split("## Few-Shot Examples")[-1].strip()
    for line in examples_section.split("\n"):
        line = line.strip()
        if line.startswith("User:"):
            messages.append({"role": "user", "content": line[5:].strip()})
        elif line.startswith("Assistant:"):
            messages.append({"role": "assistant", "content": line[10:].strip()})

    messages.append({"role": "user", "content": preprocessed})

    content, _ = await chat_completion_async(
        messages=messages,
        task_type="entity_extract",
        json_schema=_ENTITIES_SCHEMA,
        extra_log={"trace_id": trace_id},
    )

    try:
        raw = json.loads(content)
        # Ensure items is not empty for non-cancel intents
        if not raw.get("items") and raw.get("intent_modifier") != "cancel":
            raw["items"] = [{"product_query": preprocessed, "quantity": 1, "note": None}]
        return OrderEntities.model_validate(raw)
    except Exception as exc:
        logger.warning(
            "entity_extract_parse_error",
            extra={"raw": content[:200], "error": str(exc)},
        )
        # Fallback: treat entire message as one item
        return OrderEntities(
            items=[{"product_query": message, "quantity": 1, "note": None}]
        )

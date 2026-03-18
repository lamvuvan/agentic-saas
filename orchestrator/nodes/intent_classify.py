"""Intent classification node — GPT-4o-mini with escalation to GPT-4o."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shared.llm_client import ESCALATION_THRESHOLD, chat_completion_async, select_model

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "intent_classify_v1.md"
_PROMPT_CACHE: str | None = None

# JSON schema for Structured Outputs
_INTENT_SCHEMA = {
    "name": "intent_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["order", "bi_query", "chitchat", "unknown"]},
            "confidence": {"type": "number"},
            "entities": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": ["string", "null"]},
                    "table_number": {"type": ["string", "null"]},
                    "product_query": {"type": ["string", "null"]},
                    "time_range": {"type": ["string", "null"]},
                },
                "required": ["customer_name", "table_number", "product_query", "time_range"],
                "additionalProperties": False,
            },
            "reasoning": {"type": "string"},
        },
        "required": ["intent", "confidence", "entities", "reasoning"],
        "additionalProperties": False,
    },
}


def _load_prompt() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _PROMPT_CACHE


async def classify_intent(
    message: str,
    history: list[dict[str, str]] | None = None,
    trace_id: str = "",
) -> dict:
    """
    Classify message intent using GPT-4o-mini; escalate to GPT-4o if confidence < threshold.

    Returns dict with: intent, confidence, entities, escalated, model_used
    """
    prompt = _load_prompt()
    system_text = prompt.split("## Few-Shot Examples")[0].strip()

    messages = [{"role": "system", "content": system_text}]

    # Inject last 3 turns as context if available
    if history:
        for turn in history[-3:]:
            messages.append({"role": turn["role"], "content": turn["content"]})

    # Parse few-shot examples from prompt
    examples_section = prompt.split("## Few-Shot Examples")[-1].strip()
    for line in examples_section.split("\n"):
        line = line.strip()
        if line.startswith("User:"):
            messages.append({"role": "user", "content": line[5:].strip()})
        elif line.startswith("Assistant:"):
            messages.append({"role": "assistant", "content": line[10:].strip()})

    messages.append({"role": "user", "content": message})

    model = select_model("intent_classify")
    content, tokens = await chat_completion_async(
        messages=messages,
        task_type="intent_classify",
        json_schema=_INTENT_SCHEMA,
        extra_log={"trace_id": trace_id},
    )

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("intent_classify_parse_error", extra={"raw": content[:200]})
        result = {"intent": "unknown", "confidence": 0.0, "entities": {}, "reasoning": "parse error"}

    escalated = False
    if result.get("confidence", 0) < ESCALATION_THRESHOLD and result.get("intent") != "chitchat":
        logger.info(
            "intent_classify_escalate",
            extra={"confidence": result.get("confidence"), "trace_id": trace_id},
        )
        content2, _ = await chat_completion_async(
            messages=messages,
            task_type="intent_classify",
            json_schema=_INTENT_SCHEMA,
            model_override="gpt-4o",
            extra_log={"trace_id": trace_id, "escalated": True},
        )
        try:
            result = json.loads(content2)
        except json.JSONDecodeError:
            pass
        escalated = True
        model = "gpt-4o"

    return {
        "intent": result.get("intent", "unknown"),
        "confidence": result.get("confidence", 0.0),
        "entities": result.get("entities", {}),
        "escalated": escalated,
        "model_used": model,
    }

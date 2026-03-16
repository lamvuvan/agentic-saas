"""Shared OpenAI LLM client with model routing, retry logic, and token tracking."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from openai import AsyncOpenAI, RateLimitError

logger = logging.getLogger(__name__)

# Model routing table (research.md Decision 5)
_MODEL_ROUTING: dict[str, str] = {
    "intent_classify": "fast",
    "entity_extract": "fast",
    "orchestrator_plan": "smart",
    "nl2sql": "smart",
    "response_format": "fast",
    "product_rerank": "fast",
}

# Confidence threshold — below this, fast tasks escalate to smart model
ESCALATION_THRESHOLD = 0.72

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def select_model(task_type: str) -> str:
    """Return the model ID for a given task type."""
    tier = _MODEL_ROUTING.get(task_type, "fast")
    if tier == "smart":
        return os.environ.get("OPENAI_MODEL_SMART", "gpt-4o")
    return os.environ.get("OPENAI_MODEL_FAST", "gpt-4o-mini")


async def chat_completion_async(
    messages: list[dict[str, str]],
    task_type: str,
    json_schema: dict[str, Any] | None = None,
    model_override: str | None = None,
    max_retries: int = 3,
    extra_log: dict[str, Any] | None = None,
) -> tuple[str, dict[str, int]]:
    """
    Async chat completion with retry and structured output support.

    Returns:
        (content_str, token_usage) where token_usage = {prompt_tokens, completion_tokens}

    Always uses temperature=0 for determinism.
    Uses json_schema Structured Outputs when json_schema is provided (not json_object mode).
    """
    model = model_override or select_model(task_type)
    client = get_client()

    response_format: dict[str, Any] | None = None
    if json_schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": json_schema,
        }

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format

            response = await client.chat.completions.create(**kwargs)

            usage = response.usage
            token_info = {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            }
            content = response.choices[0].message.content or ""

            log_fields: dict[str, Any] = {
                "task_type": task_type,
                "model": model,
                **token_info,
            }
            if extra_log:
                log_fields.update(extra_log)
            logger.info("llm_call_complete", extra=log_fields)

            return content, token_info

        except RateLimitError as exc:
            last_exc = exc
            wait = 2**attempt
            logger.warning("llm_rate_limit", extra={"attempt": attempt, "wait_s": wait})
            await asyncio.sleep(wait)
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = 2**attempt
                logger.warning(
                    "llm_error_retry",
                    extra={"attempt": attempt, "error": str(exc), "wait_s": wait},
                )
                await asyncio.sleep(wait)
            else:
                break

    raise RuntimeError(f"LLM call failed after {max_retries} attempts: {last_exc}") from last_exc

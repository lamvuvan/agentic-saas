"""Aggregate node — synthesizes Domain Agent results into a user reply."""

from __future__ import annotations

import logging
from typing import Any

from shared.llm_client import chat_completion_async

logger = logging.getLogger(__name__)

_CHITCHAT_SYSTEM = (
    "You are a friendly assistant for a Vietnamese restaurant. "
    "Respond warmly in Vietnamese to casual conversation. Keep responses short (1-2 sentences)."
)

_AGGREGATE_SYSTEM = (
    "You are the final response synthesizer. "
    "Given a Domain Agent result, produce a clear, friendly Vietnamese response for restaurant staff. "
    "Be concise. Do not repeat the raw data — summarize it naturally."
)


async def handle_chitchat(message: str, history: list[dict[str, str]] | None = None) -> str:
    messages = [{"role": "system", "content": _CHITCHAT_SYSTEM}]
    if history:
        for turn in history[-3:]:
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    content, _ = await chat_completion_async(
        messages=messages,
        task_type="response_format",
    )
    return content.strip()


async def aggregate_results(
    message: str,
    intent: str,
    agent_results: list[dict[str, Any]],
    history: list[dict[str, str]] | None = None,
) -> tuple[str, bool]:
    """
    Synthesize agent results into a final user reply.

    Returns:
        (reply_text, requires_input)
    """
    if intent == "chitchat":
        reply = await handle_chitchat(message, history)
        return reply, False

    if not agent_results:
        return "Xin lỗi, tôi không thể xử lý yêu cầu của bạn lúc này. Vui lòng thử lại.", False

    result_entry = agent_results[0]

    # input-required: relay the prompt directly
    if result_entry.get("status") == "input-required":
        return result_entry.get("input_request", "Vui lòng xác nhận."), True

    # completed: synthesize
    domain_result = result_entry.get("result", {})
    output = domain_result.get("output", domain_result)
    reasoning = domain_result.get("reasoning_summary", "")

    # For order agent — the output may already contain a formatted message
    if isinstance(output, dict) and "message" in output:
        return output["message"], output.get("status") in ("preview", "awaiting_confirm")

    # General synthesis via GPT-4o-mini
    context = f"User asked: {message}\nAgent result: {output}\nAgent reasoning: {reasoning}"
    messages = [
        {"role": "system", "content": _AGGREGATE_SYSTEM},
        {"role": "user", "content": context},
    ]
    content, _ = await chat_completion_async(
        messages=messages,
        task_type="response_format",
    )
    return content.strip(), False

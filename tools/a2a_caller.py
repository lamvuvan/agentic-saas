"""Tool for delegating tasks to an external A2A-compatible agent via HTTP.

Usage (created automatically by the orchestrator for each remote_only agent)::

    tool = A2ACallerTool(
        name="kiot_chatbot_a2a",
        description="KiotViet customer support ...",
        agent_url="http://kiotchatbot-agent:8001",
    )
    result = await tool._arun("How do I set up inventory?")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field

from src.a2a.models import Task, TaskMessage, TaskSendParams
from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class _A2ACallerInput(BaseModel):
    message: str = Field(description="The message / question to send to the agent")
    session_id: str | None = Field(
        default=None,
        description="Optional session ID for conversation continuity",
    )


class A2ACallerTool(BaseTool):
    """LangChain tool that delegates to an external A2A agent via HTTP POST.

    The tool sends a ``TaskSendParams`` payload to ``{agent_url}/a2a/tasks``
    and returns the text content of the completed task's response message.
    """

    name: str = "a2a_caller"
    description: str = "Call an external A2A agent"
    agent_url: str
    args_schema: type[BaseModel] = _A2ACallerInput

    async def _arun(
        self,
        message: str,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        params = TaskSendParams(
            id=str(uuid.uuid4()),
            sessionId=session_id,
            message=TaskMessage(
                role="user",
                parts=[{"type": "text", "text": message}],
            ),
        )
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.agent_url.rstrip('/')}/a2a/tasks",
                    json=params.model_dump(mode="json"),
                )
                resp.raise_for_status()
                task = Task(**resp.json())
        except httpx.HTTPError as exc:
            logger.error("A2A call to %s failed: %s", self.agent_url, exc)
            return f"[A2A Error] Could not reach external agent at {self.agent_url}: {exc}"

        if task.status.message:
            return task.status.message.text()
        return ""

    def _run(self, **kwargs: Any) -> str:  # pragma: no cover
        raise NotImplementedError("A2ACallerTool is async-only; use _arun.")

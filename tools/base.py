"""Base tool interface — LangChain-compatible."""

from __future__ import annotations

from pydantic import ConfigDict
from langchain_core.tools import BaseTool as LCBaseTool


class BaseTool(LCBaseTool):
    """Application-level base tool.

    Extends LangChain's BaseTool so every tool in this project is
    automatically compatible with the LangChain / LangGraph ecosystem
    (can be used in ``create_deep_agent``, tool calling, etc.).

    Subclasses MUST implement ``_arun`` (async). ``_run`` should raise
    ``NotImplementedError`` — LangChain always dispatches to the async
    path when running inside an async event loop (FastAPI / uvicorn).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

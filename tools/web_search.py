"""Web search tool — wraps Tavily / generic search API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """Search the web for real-time information."""

    name: str = "web_search"
    description: str = (
        "Search the web for real-time information. "
        "Input should be a search query string. "
        "Returns a list of relevant results with titles, URLs, and snippets."
    )

    provider: str = "tavily"
    max_results: int = 5
    api_key: str = ""

    def _run(self, query: str) -> str:
        """Sync path is not supported — LangChain always dispatches to ``_arun`` in async contexts."""
        raise NotImplementedError("WebSearchTool is async-only; use _arun.")

    async def _arun(self, query: str) -> str:
        """Execute web search asynchronously."""
        if self.provider == "tavily":
            return await self._search_tavily(query)
        return f"Search provider '{self.provider}' not implemented yet."

    async def _search_tavily(self, query: str) -> str:
        if not self.api_key:
            return "Tavily API key not configured."

        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": self.max_results,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("results", [])
        if not results:
            return "No results found."

        formatted = []
        for r in results[:self.max_results]:
            formatted.append(
                f"Title: {r.get('title', 'N/A')}\n"
                f"URL: {r.get('url', '')}\n"
                f"Content: {r.get('content', '')[:300]}"
            )
        return "\n\n---\n\n".join(formatted)

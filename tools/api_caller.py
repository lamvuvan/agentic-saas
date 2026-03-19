"""REST API caller tool with domain whitelist."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import httpx

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class APICallerTool(BaseTool):
    """Call external REST APIs with configurable domain whitelist."""

    name: str = "api_caller"
    description: str = (
        "Call an external REST API. "
        "Input should be a JSON string with keys: url (required), method (GET/POST, default GET), "
        "headers (dict, optional), body (dict, optional)."
    )

    timeout: int = 30
    max_response_size: int = 10000
    allowed_domains: list[str] = []

    def _run(self, query: str) -> str:
        """Sync path is not supported — LangChain always dispatches to ``_arun`` in async contexts."""
        raise NotImplementedError("APICallerTool is async-only; use _arun.")

    async def _arun(self, query: str) -> str:
        """Parse the input JSON and execute the HTTP request."""
        try:
            params = json.loads(query)
        except json.JSONDecodeError:
            return "Invalid JSON input. Expected: {\"url\": \"...\", \"method\": \"GET\"}"

        url = params.get("url", "")
        if not url:
            return "Missing 'url' in input."

        # Domain whitelist check
        parsed = urlparse(url)
        if self.allowed_domains and parsed.hostname not in self.allowed_domains:
            return (
                f"Domain '{parsed.hostname}' is not in the allowed list: "
                f"{self.allowed_domains}"
            )

        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        body = params.get("body")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body if method in ("POST", "PUT", "PATCH") else None,
                )
                text = response.text[:self.max_response_size]
                return (
                    f"Status: {response.status_code}\n"
                    f"Response:\n{text}"
                )
        except httpx.TimeoutException:
            return f"Request to {url} timed out after {self.timeout}s."
        except Exception as exc:
            return f"API call failed: {exc}"

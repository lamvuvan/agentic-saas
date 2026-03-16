"""ToolRegistryClient — shared library for agents to discover and execute tools.

Token forwarding: Bearer token is read from ContextVar (set by AuthForwardMiddleware
or caller) and forwarded transparently to the Tool Registry. Never stored.
"""
from __future__ import annotations

from typing import Any

import httpx


class ToolRegistryError(Exception):
    """Raised when Tool Registry returns a non-200 response."""

    def __init__(self, message: str, error_code: str, tool_name: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.tool_name = tool_name


class ToolRegistryClient:
    """HTTP client for Tool Registry v1 API.

    Usage:
        client = ToolRegistryClient(base_url="http://tool-registry:8001")
        tools = await client.get_openai_tools(namespace="customer")
        result = await client.execute("customer__get_customers", {"query": "Lâm"})
    """

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def get_openai_tools(self, namespace: str | None = None) -> list[dict[str, Any]]:
        """Return registered tools in OpenAI function-call format.

        Args:
            namespace: Optional filter (e.g. "customer", "bi").

        Returns:
            List of dicts with shape {"type": "function", "function": {...}}.
        """
        params: dict[str, str] = {}
        if namespace is not None:
            params["namespace"] = namespace

        async with httpx.AsyncClient() as http:
            response = await http.get(f"{self._base_url}/tools", params=params)
            response.raise_for_status()
            return response.json()

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a named tool, forwarding the caller's Bearer token.

        Args:
            name:   Tool name (namespace__verb format).
            params: Key-value parameters matching the tool's parameter schema.

        Returns:
            The 'result' field from the ExecutionResponse.

        Raises:
            ToolRegistryError: When the registry returns a non-200 response.
        """
        from shared.auth_context import get_token

        token = get_token()
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{self._base_url}/tools/{name}/execute",
                json=params,
                headers=headers,
            )

        if not response.is_success:
            body = response.json()
            raise ToolRegistryError(
                message=body.get("error", f"Tool Registry returned {response.status_code}"),
                error_code=body.get("error_code", "UNKNOWN"),
                tool_name=body.get("tool_name", name),
            )

        return response.json().get("result")

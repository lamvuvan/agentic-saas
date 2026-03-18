"""AgentRegistry — dynamic discovery of Domain Agents via Agent Card polling."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Fetch and cache Agent Cards from seed URLs at startup, refresh every 60s.

    Usage:
        registry = AgentRegistry.from_env()
        await registry.start()          # call once at lifespan startup
        manifest = registry.build_prompt_context()   # inject into plan prompt
        url = registry.get_a2a_endpoint("order-agent")
    """

    def __init__(self, seed_urls: list[str], refresh_interval: int = 60) -> None:
        self._seed_urls = [u.rstrip("/") for u in seed_urls if u.strip()]
        self._refresh_interval = refresh_interval
        # card["name"] -> card dict
        self._agents: dict[str, dict[str, Any]] = {}
        # card["name"] of agents whose last fetch succeeded
        self._healthy: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def from_env(cls) -> "AgentRegistry":
        """Build registry from AGENT_SEED_URLS env var (comma-separated base URLs)."""
        raw = os.environ.get("AGENT_SEED_URLS", "")
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        return cls(seed_urls=urls)

    async def start(self) -> None:
        """Fetch agent cards immediately, then schedule background refresh."""
        await self._refresh()
        self._task = asyncio.create_task(self._background_refresh())
        logger.info(
            "agent_registry_started",
            extra={"agents": list(self._agents.keys()), "healthy": list(self._healthy)},
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _background_refresh(self) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval)
            await self._refresh()

    async def _refresh(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in self._seed_urls:
                try:
                    r = await client.get(f"{url}/.well-known/agent.json")
                    r.raise_for_status()
                    card: dict[str, Any] = r.json()
                    name: str = card["name"]
                    # Store a2a_endpoint derived from the base URL if not in card
                    if "a2a_endpoint" not in card:
                        card["a2a_endpoint"] = f"{url}/a2a/tasks"
                    self._agents[name] = card
                    self._healthy.add(name)
                    logger.debug("agent_card_refreshed", extra={"agent": name, "url": url})
                except Exception as exc:
                    # Mark any agent previously known at this URL as unhealthy
                    name = self._name_from_url(url)
                    if name:
                        self._healthy.discard(name)
                    logger.warning(
                        "agent_card_fetch_failed",
                        extra={"url": url, "error": str(exc)},
                    )

    def _name_from_url(self, url: str) -> str | None:
        """Return the agent name previously fetched from this base URL."""
        for name, card in self._agents.items():
            if card.get("url", "").rstrip("/") == url:
                return name
        return None

    def build_prompt_context(self) -> str:
        """Render healthy agents and their skills as markdown for prompt injection."""
        healthy_agents = [v for k, v in self._agents.items() if k in self._healthy]
        if not healthy_agents:
            return "## Available Domain Agents\n\n_(none currently available)_\n"

        lines = ["## Available Domain Agents\n"]
        for agent in healthy_agents:
            lines.append(f"### {agent['name']} (v{agent.get('version', '1.0.0')})")
            if agent.get("description"):
                lines.append(f"{agent['description']}\n")
            for skill in agent.get("skills", []):
                lines.append(f"- Skill `{skill['id']}`: {skill.get('description', '')}")
                examples = skill.get("examples", [])[:2]
                if examples:
                    lines.append(f"  Examples: {'; '.join(examples)}")
            a2a = agent.get("a2a_endpoint", "")
            lines.append(f"A2A endpoint: {a2a}\n")
        return "\n".join(lines)

    def get_a2a_endpoint(self, agent_name: str) -> str | None:
        """Return the A2A endpoint URL for a healthy agent, or None if unavailable."""
        card = self._agents.get(agent_name)
        if card and agent_name in self._healthy:
            return card.get("a2a_endpoint")
        return None

    def is_healthy(self, agent_name: str) -> bool:
        return agent_name in self._healthy

    @property
    def healthy_agents(self) -> list[str]:
        return list(self._healthy)

"""MemoryService — shared semantic and episodic memory for Domain Agents.

Layer 2 (Semantic): agent_memory table — facts accumulated over time.
Layer 3 (Episodic): agent_task_history table — past task outcomes.

When db is None all methods silently return empty results (graceful degradation
when POSTGRES_DSN is not configured).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


class MemoryService:
    """Retrieve and store domain agent memories from PostgreSQL."""

    def __init__(self, db: "asyncpg.Pool", agent_name: str) -> None:
        self._db = db
        self._agent_name = agent_name

    async def retrieve(
        self,
        tenant_id: str,
        query_context: dict[str, Any],
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return semantic memories most relevant to the current task.

        Ordered by: confidence DESC, usage_count DESC, last_used_at DESC.
        Bumps usage_count + last_used_at for every returned row.
        """
        if self._db is None:
            return []
        keyword = query_context.get("keyword", "")
        try:
            rows = await self._db.fetch(
                """
                SELECT id, memory_type, key, content, confidence, usage_count
                FROM agent_memory
                WHERE agent_name = $1
                  AND tenant_id  = $2
                  AND (key ILIKE $3 OR content ILIKE $3)
                ORDER BY confidence DESC, usage_count DESC, last_used_at DESC NULLS LAST
                LIMIT $4
                """,
                self._agent_name,
                tenant_id,
                f"%{keyword}%",
                limit,
            )
            ids = [r["id"] for r in rows]
            if ids:
                await self._bump_usage(ids)
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("memory_retrieve_failed", extra={"error": str(exc)})
            return []

    async def store(
        self,
        tenant_id: str,
        memory_type: str,
        key: str,
        content: str,
        confidence: float = 0.8,
    ) -> None:
        """Upsert a semantic fact.

        On conflict takes GREATEST(existing_confidence, new_confidence).
        """
        if self._db is None:
            return
        try:
            await self._db.execute(
                """
                INSERT INTO agent_memory
                    (agent_name, tenant_id, memory_type, key, content, confidence)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (agent_name, tenant_id, memory_type, key)
                DO UPDATE SET
                    content    = EXCLUDED.content,
                    confidence = GREATEST(agent_memory.confidence, EXCLUDED.confidence),
                    updated_at = now()
                """,
                self._agent_name,
                tenant_id,
                memory_type,
                key,
                content,
                confidence,
            )
        except Exception as exc:
            logger.warning("memory_store_failed", extra={"error": str(exc)})

    async def find_similar_tasks(
        self,
        tenant_id: str,
        skill: str,
        input_summary: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return successful past tasks whose input_summary matches the keyword prefix."""
        if self._db is None:
            return []
        keyword = input_summary[:30] if input_summary else ""
        try:
            rows = await self._db.fetch(
                """
                SELECT input_summary, key_decisions, learnings, duration_ms
                FROM agent_task_history
                WHERE agent_name = $1
                  AND tenant_id  = $2
                  AND skill      = $3
                  AND outcome    = 'success'
                  AND input_summary ILIKE $4
                ORDER BY created_at DESC
                LIMIT $5
                """,
                self._agent_name,
                tenant_id,
                skill,
                f"%{keyword}%",
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("find_similar_tasks_failed", extra={"error": str(exc)})
            return []

    async def record_task(
        self,
        tenant_id: str,
        plan_id: str | None,
        skill: str,
        input_summary: str,
        outcome: str,
        key_decisions: dict[str, Any],
        learnings: str,
        duration_ms: int,
    ) -> None:
        """Insert an episodic task history row.

        input_summary MUST NOT contain raw PII (customer names, phone numbers).
        """
        if self._db is None:
            return
        try:
            await self._db.execute(
                """
                INSERT INTO agent_task_history
                    (agent_name, tenant_id, plan_id, skill, input_summary,
                     outcome, key_decisions, learnings, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                self._agent_name,
                tenant_id,
                plan_id,
                skill,
                input_summary,
                outcome,
                json.dumps(key_decisions),
                learnings,
                duration_ms,
            )
        except Exception as exc:
            logger.warning("record_task_failed", extra={"error": str(exc)})

    async def _bump_usage(self, ids: list[Any]) -> None:
        """Increment usage_count and update last_used_at for retrieved rows."""
        if not ids:
            return
        try:
            placeholders = ", ".join(f"${i + 1}" for i in range(len(ids)))
            await self._db.execute(
                f"""
                UPDATE agent_memory
                SET usage_count  = usage_count + 1,
                    last_used_at = now()
                WHERE id IN ({placeholders})
                """,
                *ids,
            )
        except Exception as exc:
            logger.debug("bump_usage_failed", extra={"error": str(exc)})

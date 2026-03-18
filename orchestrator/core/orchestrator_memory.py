"""OrchestratorMemoryService — meta-level routing memory for the Orchestrator.

Unlike MemoryService (domain facts for Order/BI agents), this service stores
routing strategies, plan templates, and user patterns at the Orchestrator level:

Semantic layer  — orchestrator_memory table (migration 003):
    routing_pattern : intent_class → best agent combination
    plan_template   : request structure → optimal sub_goal layout
    user_pattern    : tenant behaviour pattern
    routing_fix     : correction when a routing strategy previously failed

Episodic layer  — REUSES existing plans + plan_sub_goals tables (no new table).
    find_similar_plans() queries completed plans for structural reuse.

When db is None all methods silently return empty results / no-op
(graceful degradation when POSTGRES_DSN is not configured).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from shared.llm_client import chat_completion_async

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Routing learning extraction prompt
# ---------------------------------------------------------------------------

ROUTING_LEARNINGS_PROMPT = """Plan vừa hoàn thành. Extract tối đa 3 routing insights.

Output JSON: [{"memory_type": ..., "key": ..., "content": ..., "confidence": ...}]

Các loại insight:
- routing_pattern: "intent X → agent combo A+B hiệu quả"
  key=intent_class, content=combo + lý do ngắn gọn
- plan_template: "request dạng Y → cấu trúc sub_goals này tối ưu"
  key=request_pattern, content=template
- user_pattern: "user hay kết hợp X+Y → nên confirm trước"
  key=behavior, content=pattern + action
- routing_fix: "routing Z thất bại → thử W"
  key=failure_pattern, content=correction

Nếu không có insight đáng lưu → trả về [].

Plan goal: {goal}
Sub-goals: {sub_goals}
"""

_LEARNINGS_SCHEMA = {
    "name": "routing_learnings",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "memory_type": {"type": "string"},
                        "key": {"type": "string"},
                        "content": {"type": "string"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["memory_type", "key", "content", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["facts"],
        "additionalProperties": False,
    },
}


class OrchestratorMemoryService:
    """Retrieve and store Orchestrator-level routing patterns."""

    def __init__(self, db: "asyncpg.Pool | None") -> None:
        self._db = db

    # ── Semantic: routing patterns ─────────────────────────────────────────

    async def retrieve_patterns(
        self,
        tenant_id: str,
        intent_class: str,
        request_summary: str,
        limit: int = 4,
    ) -> list[dict[str, Any]]:
        """Return routing patterns relevant to the current intent and request.

        Ordered by: confidence DESC, usage_count DESC.
        """
        if self._db is None:
            return []
        try:
            rows = await self._db.fetch(
                """
                SELECT memory_type, key, content, confidence
                FROM orchestrator_memory
                WHERE tenant_id = $1
                  AND (key ILIKE $2 OR content ILIKE $3)
                ORDER BY confidence DESC, usage_count DESC
                LIMIT $4
                """,
                tenant_id,
                f"%{intent_class}%",
                f"%{request_summary[:40]}%",
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("orch_memory_retrieve_failed", extra={"error": str(exc)})
            return []

    async def store_pattern(
        self,
        tenant_id: str,
        memory_type: str,
        key: str,
        content: str,
        confidence: float = 0.8,
    ) -> None:
        """Upsert a routing fact.

        On conflict takes GREATEST(existing_confidence, new_confidence).
        """
        if self._db is None:
            return
        try:
            await self._db.execute(
                """
                INSERT INTO orchestrator_memory
                    (tenant_id, memory_type, key, content, confidence)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, memory_type, key)
                DO UPDATE SET
                    content    = EXCLUDED.content,
                    confidence = GREATEST(orchestrator_memory.confidence, EXCLUDED.confidence),
                    updated_at = now()
                """,
                tenant_id,
                memory_type,
                key,
                content,
                confidence,
            )
        except Exception as exc:
            logger.warning("orch_memory_store_failed", extra={"error": str(exc)})

    # ── Episodic: reuse plans + plan_sub_goals (no new table) ─────────────

    async def find_similar_plans(
        self,
        tenant_id: str,
        user_message: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return recently completed plans with similar user_message.

        Reuses the existing plans + plan_sub_goals tables — no extra table needed.
        """
        if self._db is None:
            return []
        keyword = user_message[:40] if user_message else ""
        try:
            rows = await self._db.fetch(
                """
                SELECT p.goal, p.user_message,
                       json_agg(sg ORDER BY sg.sequence) AS sub_goals
                FROM plans p
                JOIN plan_sub_goals sg ON sg.plan_id = p.id
                WHERE p.tenant_id   = $1
                  AND p.status      = 'completed'
                  AND p.user_message ILIKE $2
                GROUP BY p.id
                ORDER BY p.created_at DESC
                LIMIT $3
                """,
                tenant_id,
                f"%{keyword}%",
                limit,
            )
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("find_similar_plans_failed", extra={"error": str(exc)})
            return []

    # ── Learning: extract routing insights after plan completes ───────────

    async def extract_and_store(
        self,
        tenant_id: str,
        plan: dict[str, Any],
        sub_goals: list[dict[str, Any]],
    ) -> None:
        """Extract ≤ 3 routing insights and upsert to orchestrator_memory.

        Called after plan.status = completed.
        Best-effort — any failure is logged but never propagated.
        No-op when db is None.
        """
        if self._db is None:
            return
        try:
            facts = await self._call_extract_llm(plan, sub_goals)
            for fact in facts:
                await self.store_pattern(
                    tenant_id=tenant_id,
                    memory_type=fact["memory_type"],
                    key=fact["key"],
                    content=fact["content"],
                    confidence=fact.get("confidence", 0.8),
                )
        except Exception as exc:
            logger.warning("routing_extract_failed", extra={"error": str(exc)})

    async def _call_extract_llm(
        self,
        plan: dict[str, Any],
        sub_goals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Call GPT-4o-mini to extract routing insights from a completed plan."""
        goal = plan.get("goal", "")
        sub_goals_str = json.dumps(
            [{"agent": sg.get("agent_name", ""), "title": sg.get("title", "")} for sg in sub_goals],
            ensure_ascii=False,
        )
        prompt = ROUTING_LEARNINGS_PROMPT.format(goal=goal, sub_goals=sub_goals_str)
        content, _ = await chat_completion_async(
            messages=[{"role": "user", "content": prompt}],
            task_type="extract_routing_learnings",
            json_schema=_LEARNINGS_SCHEMA,
        )
        try:
            parsed = json.loads(content)
            facts = parsed.get("facts", [])
            # Cap at 3 facts
            return facts[:3]
        except (json.JSONDecodeError, AttributeError):
            return []

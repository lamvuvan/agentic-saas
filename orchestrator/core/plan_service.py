"""PlanService — persists and syncs Orchestrator plans to PostgreSQL.

Implements the display-layer plan lifecycle:
  create_plan()      → insert plans + plan_sub_goals rows immediately after Plan node
  link_task()        → link A2A task_id to a sub_goal after POST /a2a/tasks succeeds
  sync_from_task()   → sync sub_goal status from A2A task polling; auto-complete parent plan
  get_plan()         → read plan + sub_goals for API responses
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class PlanService:
    def __init__(self, db: asyncpg.Pool) -> None:
        self.db = db

    async def create_plan(
        self,
        session_id: str,
        tenant_id: str,
        user_message: str,
        display: dict[str, Any],
    ) -> str:
        """
        Insert a plans row (status=running) and all plan_sub_goals rows.

        Called immediately after the Plan node generates the dual-layer output,
        before any A2A task is dispatched.

        Returns the new plan_id (UUID as str).
        """
        plan_id = await self.db.fetchval(
            """
            INSERT INTO plans(session_id, tenant_id, user_message, goal, status)
            VALUES($1, $2, $3, $4, 'running')
            RETURNING id
            """,
            session_id,
            tenant_id,
            user_message,
            display["goal"],
        )

        for sg in display.get("sub_goals", []):
            await self.db.execute(
                """
                INSERT INTO plan_sub_goals(plan_id, sequence, title, agent_name, agent_label)
                VALUES($1, $2, $3, $4, $5)
                """,
                plan_id,
                sg["sequence"],
                sg["title"],
                sg["agent_name"],
                sg["agent_label"],
            )

        logger.info(
            "plan_created",
            extra={
                "plan_id": str(plan_id),
                "session_id": session_id,
                "sub_goals": len(display.get("sub_goals", [])),
            },
        )
        return str(plan_id)

    async def link_task(self, plan_id: str, sequence: int, a2a_task_id: str) -> None:
        """
        Associate an A2A task_id with a sub_goal and set status=running.

        Called immediately after POST /a2a/tasks returns 202 with a task_id.
        """
        await self.db.execute(
            """
            UPDATE plan_sub_goals
            SET a2a_task_id = $3, status = 'running', started_at = now()
            WHERE plan_id = $1 AND sequence = $2
            """,
            plan_id,
            sequence,
            a2a_task_id,
        )

    async def sync_from_task(
        self,
        a2a_task_id: str,
        task_status: str,
        result_summary: str | None = None,
    ) -> None:
        """
        Update sub_goal status based on the current A2A task status.

        Looked up via idx_sub_goals_task — no plan_id/sequence required.
        Calls _maybe_complete_plan() to auto-finalize the parent plan.
        """
        status_map = {
            "working": "running",
            "completed": "completed",
            "failed": "failed",
            "timeout": "failed",
        }
        status = status_map.get(task_status)
        if status is None:
            return

        await self.db.execute(
            """
            UPDATE plan_sub_goals
            SET status         = $2,
                result_summary = $3,
                completed_at   = CASE WHEN $2 != 'running' THEN now() ELSE completed_at END
            WHERE a2a_task_id = $1
            """,
            a2a_task_id,
            status,
            result_summary,
        )
        await self._maybe_complete_plan(a2a_task_id)

    async def _maybe_complete_plan(self, a2a_task_id: str) -> None:
        """
        If all sub_goals for this plan are in a terminal state, update plans.status.
        Uses idx_sub_goals_task to find the parent plan without needing plan_id.
        """
        row = await self.db.fetchrow(
            """
            SELECT
                p.id,
                COUNT(*) FILTER (WHERE sg.status NOT IN ('completed', 'failed')) AS pending_count,
                COUNT(*) FILTER (WHERE sg.status = 'failed')                    AS failed_count
            FROM plan_sub_goals sg
            JOIN plans p ON sg.plan_id = p.id
            WHERE sg.a2a_task_id = $1
            GROUP BY p.id
            """,
            a2a_task_id,
        )
        if row is None:
            return

        if row["pending_count"] == 0:
            new_status = "failed" if row["failed_count"] > 0 else "completed"
            await self.db.execute(
                "UPDATE plans SET status = $2, updated_at = now() WHERE id = $1",
                row["id"],
                new_status,
            )
            logger.info(
                "plan_finalized",
                extra={"plan_id": str(row["id"]), "status": new_status},
            )

    async def get_plan(self, plan_id: str) -> dict[str, Any]:
        """
        Fetch a plan and its sub_goals by plan_id.

        Returns {} if the plan is not found (caller should raise 404).
        """
        plan = await self.db.fetchrow("SELECT * FROM plans WHERE id = $1", plan_id)
        if plan is None:
            return {}

        subs = await self.db.fetch(
            "SELECT * FROM plan_sub_goals WHERE plan_id = $1 ORDER BY sequence",
            plan_id,
        )
        return {
            "plan": dict(plan),
            "sub_goals": [dict(s) for s in subs],
        }

-- Migration 001: Plan persistence tables for Plan Visibility (US7)
-- Run on Orchestrator startup via asyncpg.

CREATE TABLE IF NOT EXISTS plans (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   VARCHAR(128) NOT NULL,
    tenant_id    VARCHAR(128) NOT NULL,
    user_message TEXT        NOT NULL,
    goal         TEXT        NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending|running|completed|failed
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plan_sub_goals (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id        UUID        REFERENCES plans(id) ON DELETE CASCADE,
    sequence       INT         NOT NULL,
    title          TEXT        NOT NULL,
    agent_name     VARCHAR(100) NOT NULL,   -- internal: "bi-agent", used for routing/debug
    agent_label    VARCHAR(100) NOT NULL,   -- display: "Báo Cáo & Phân Tích"
    a2a_task_id    VARCHAR(128),            -- NULL until dispatched via link_task()
    status         VARCHAR(20) NOT NULL DEFAULT 'pending',
    result_summary TEXT,
    started_at     TIMESTAMPTZ,
    completed_at   TIMESTAMPTZ
);

-- Efficient lookup of latest plan for a session
CREATE INDEX IF NOT EXISTS idx_plans_session
    ON plans (session_id, tenant_id);

-- Efficient traversal of sub-goals in order
CREATE INDEX IF NOT EXISTS idx_sub_goals_plan
    ON plan_sub_goals (plan_id, sequence);

-- Reverse lookup: a2a_task_id → sub_goal (used by sync_from_task)
CREATE INDEX IF NOT EXISTS idx_sub_goals_task
    ON plan_sub_goals (a2a_task_id);

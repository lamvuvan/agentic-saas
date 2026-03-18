-- 002_agent_memory.sql
-- Domain Agent Memory: Layer 2 (Semantic) + Layer 3 (Episodic)
-- Idempotent: all CREATE statements use IF NOT EXISTS

-- ---------------------------------------------------------------------------
-- Layer 2: Semantic Memory — domain facts accumulated over time
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_memory (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name    VARCHAR(100) NOT NULL,                    -- "order-agent" | "bi-agent"
    tenant_id     VARCHAR(128) NOT NULL,
    memory_type   VARCHAR(50)  NOT NULL,
    -- order-agent: product_alias | customer_pref | order_pattern | vn_expression
    -- bi-agent:    sql_pattern   | glossary_fix  | column_alias  | query_template
    key           TEXT         NOT NULL,                    -- lookup key
    content       TEXT         NOT NULL,                    -- fact content
    confidence    FLOAT        NOT NULL DEFAULT 0.8,
    usage_count   INT          NOT NULL DEFAULT 0,
    last_used_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),

    UNIQUE (agent_name, tenant_id, memory_type, key)
);

CREATE INDEX IF NOT EXISTS idx_mem_agent_tenant ON agent_memory (agent_name, tenant_id);
CREATE INDEX IF NOT EXISTS idx_mem_type_key     ON agent_memory (memory_type, key);

-- ---------------------------------------------------------------------------
-- Layer 3: Episodic Memory — past task outcomes
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_task_history (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name     VARCHAR(100) NOT NULL,
    tenant_id      VARCHAR(128) NOT NULL,
    plan_id        UUID         REFERENCES plans(id) ON DELETE SET NULL,
    skill          VARCHAR(100) NOT NULL,              -- "create_order" | "bi_query"
    input_summary  TEXT         NOT NULL,              -- sanitised — NO raw PII
    outcome        VARCHAR(20)  NOT NULL,              -- "success" | "failed" | "cancelled"
    key_decisions  JSONB,                              -- {tool_name: arguments}
    learnings      TEXT,                               -- JSON array of extracted facts
    duration_ms    INT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_history_agent ON agent_task_history (agent_name, tenant_id);
CREATE INDEX IF NOT EXISTS idx_history_skill ON agent_task_history (skill, outcome);
CREATE INDEX IF NOT EXISTS idx_history_plan  ON agent_task_history (plan_id);

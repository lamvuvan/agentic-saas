-- Migration 003: orchestrator_memory — Semantic memory for Orchestrator routing patterns
-- Idempotent: safe to run on every startup (CREATE TABLE/INDEX IF NOT EXISTS)

CREATE TABLE IF NOT EXISTS orchestrator_memory (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   VARCHAR(128) NOT NULL,
    memory_type VARCHAR(50)  NOT NULL,
    -- "routing_pattern" : intent_class → agent combination optimal
    -- "plan_template"   : plan structure optimal for specific request type
    -- "user_pattern"    : user/tenant request combination behaviour
    -- "routing_fix"     : correction when routing failed previously
    key         TEXT         NOT NULL,
    content     TEXT         NOT NULL,
    confidence  FLOAT        NOT NULL DEFAULT 0.8,
    usage_count INT          NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Unique constraint enables idempotent UPSERT
CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_mem_key
    ON orchestrator_memory (tenant_id, memory_type, key);

-- Fast lookup by tenant + type for retrieve_patterns()
CREATE INDEX IF NOT EXISTS idx_orch_mem_type
    ON orchestrator_memory (tenant_id, memory_type);

-- Episodic memory: REUSE existing plans + plan_sub_goals tables.
-- OrchestratorMemoryService.find_similar_plans() queries them directly.

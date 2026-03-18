# Data Model: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Date**: 2026-03-16

---

## Entity Overview

```
UserSession ──────── has many ──────── ConversationTurn
     │                    │
     └── has one active ──┤──────────── A2ATask ◄── A2ATaskPayload (params)
     │                    │                 │
     └── has many ─────── DisplayPlan       │
                               │            │
                        PlanSubGoal ────────┘ (a2a_task_id FK)
                                            │
                          ┌─────────────────┴──────────────────┐
                    (Order Agent)                         (BI Agent)
                       OrderDraft                       BIQueryResult
                          │                                      │
                    OrderEntities                                 │
                          │                                      │
                     ProductMatch (1..N)                         │
                                                                 │
AgentMemory ─────────────── scoped per (agent_name, tenant_id) ─┘
AgentTaskHistory ─────────── links to DisplayPlan via plan_id FK

OrchestratorMemory ─── scoped per tenant_id ─── used by Plan node (routing context)
```

---

## Entities

### UserSession

Represents an active user conversation. Persisted in Redis. Expires after 30 minutes of inactivity.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `session_id` | `str` (UUID) | PK, required | Unique session identifier |
| `tenant_id` | `str` | required | Tenant identifier (v1: single-tenant, defaults to "default") |
| `created_at` | `datetime` (UTC) | required | Session creation timestamp |
| `last_active_at` | `datetime` (UTC) | required | Updated on every message; used for TTL |
| `turns` | `list[ConversationTurn]` | max 20, required | Message history; only last 3 injected into LLM context |
| `active_task_id` | `str | null` | optional | Current A2A task ID if a task is in progress |

**Storage**: Redis key `session:{session_id}` as JSON string, TTL = 30 minutes (reset on activity).

**State transitions**:
- Created on first message → active
- Updated on each turn
- Expired (TTL) → cleaned up by Redis automatically

---

### ConversationTurn

A single message-response pair within a session.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `turn_id` | `str` (UUID) | PK, required | Unique turn identifier |
| `session_id` | `str` | FK → UserSession | Parent session |
| `role` | `"user" | "assistant"` | required | Message sender |
| `content` | `str` | required, max 4096 chars | Message text |
| `timestamp` | `datetime` (UTC) | required | When message was received/sent |
| `intent` | `str | null` | optional | Classified intent for user turns |
| `trace_id` | `str` | required | Links to distributed trace |

---

### IntentClassification

Output of the Orchestrator's intent classification node.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `intent` | `"order" | "bi_query" | "chitchat" | "unknown"` | required | Classified intent |
| `confidence` | `float` | 0.0–1.0, required | Classification confidence |
| `entities` | `dict[str, Any]` | optional | Top-level entities extracted during classification |
| `escalated` | `bool` | required | Whether GPT-4o-mini was escalated to GPT-4o |
| `model_used` | `str` | required | LLM model that produced this result |

**Validation**: If `confidence < 0.72` and `intent != "chitchat"`, the Orchestrator escalates to GPT-4o and re-classifies.

---

### ExecutionPlan

The Orchestrator's serializable plan, persisted to Redis before any A2A dispatch.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `plan_id` | `str` (UUID) | PK, required | Unique plan identifier |
| `session_id` | `str` | FK → UserSession, required | Parent session |
| `goal` | `str` | required | Human-readable goal description |
| `intent` | `str` | required | Classified intent driving this plan |
| `steps` | `list[PlanStep]` | min 1, required | Ordered execution steps |
| `created_at` | `datetime` (UTC) | required | Plan creation timestamp |
| `replan_count` | `int` | default 0, max 3 | Number of replanning attempts so far |
| `max_replans` | `int` | default 3 | Maximum allowed replanning attempts |
| `status` | `"active" | "completed" | "failed" | "replanning"` | required | Plan lifecycle status |

**Storage**: Redis key `plan:{plan_id}` as JSON, TTL = 1 hour.

**State transitions**:
- `active` (created) → `completed` (all steps done) or `failed` (max replans exceeded) or `replanning` (step failed, replan triggered)

---

### PlanStep

One step within an ExecutionPlan. Generated in a single GPT-4o call by the Plan node.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `step_id` | `str` | required | Step identifier (e.g., "step-1") |
| `agent` | `"order-agent" | "bi-agent"` | required | Target Domain Agent |
| `skill` | `str` | required | A2A skill identifier (e.g., "create_order") |
| `params` | `dict[str, Any]` | required | Input parameters for the skill |
| `depends_on` | `list[str]` | default [] | Agent names (not step_ids) this step must wait for; e.g. `["order-agent"]` |
| `instructions` | `str` | default "" | Plain-language description of this step's specific goal, generated by the Plan LLM; e.g. `"Tạo đơn hàng bàn 3 gồm 3 bò kho cho khách Lâm."` |
| `status` | `"pending" | "running" | "completed" | "failed"` | required | Step execution status |
| `result` | `dict | null` | optional | Result from Domain Agent when completed |
| `task_id` | `str | null` | optional | A2A task ID once dispatched |

---

### A2ATaskPayload

The enriched task payload sent from the Orchestrator's `DispatchEngine` to a Domain Agent. Extends the basic `params` dict with full context so the Domain Agent can reason without any prior state.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `task_id` | `str` (UUID) | required | A2A task identifier (same as A2ATask.task_id) |
| `plan_id` | `str` (UUID) | required | Parent ExecutionPlan identifier |
| `sub_goal_sequence` | `int` | required | 1-indexed sequence of this step within the plan |
| `session_id` | `str` | required | Parent UserSession identifier |
| `tenant_id` | `str` | required | Tenant identifier |
| `original_message` | `str` | required | The user's original raw message that initiated the plan |
| `skill` | `str` | required | A2A skill identifier (e.g., `"create_order"`, `"bi_query"`) |
| `instructions` | `str` | required | Per-step goal from the Plan LLM; injected as `## Nhiệm Vụ Hiện Tại` in Domain Agent system prompt |
| `conversation_history` | `list[dict]` | max 6 turns, required | Last 6 conversation turns from session; each dict has `role` and `content` |
| `dependency_results` | `dict[str, dict]` | default {} | Upstream agent name → their `A2AResult.output` dict; empty for steps with no dependencies |

**Usage in Domain Agent**: `build_agent_system_prompt(payload, memory_context)` assembles these groups into the LLM system prompt — `instructions` as task goal, `original_message` for context, `dependency_results` as prior step context, then memory.

**Serialisation**: Sent as `params` dict in the A2A POST body; Domain Agent's `a2a_server.py` extracts the payload with `A2ATaskPayload.model_validate(task.params)`.

---

### A2ATask

An asynchronous work unit managed by a Domain Agent's task store.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `task_id` | `str` (UUID) | PK, required | Unique task identifier |
| `skill` | `str` | required | Skill being executed (e.g., "create_order", "bi_query") |
| `params` | `dict[str, Any]` | required | Input parameters received from Orchestrator |
| `status` | `TaskStatus` | required | See status lifecycle below |
| `result` | `A2AResult | null` | optional | Populated when status = "completed" |
| `error` | `str | null` | optional | Error message when status = "failed" |
| `input_request` | `str | null` | optional | Prompt sent to user when status = "input-required" |
| `created_at` | `datetime` (UTC) | required | Task creation timestamp |
| `updated_at` | `datetime` (UTC) | required | Last status change timestamp |

**`TaskStatus` enum**: `submitted` → `working` → `completed` | `failed` | `timeout` | `input-required`

`input-required` is used for two distinct pause scenarios:
1. **Order confirmation**: Order Agent preview waiting for staff confirmation (existing multi-turn flow).
2. **HITL mutating tool gate**: Domain Agent detected a `requires_confirmation: true` tool and is waiting for explicit staff approval before executing the write operation.

In both cases, `input_request` contains the Vietnamese message to show the user.

**Storage**: Redis hash `a2a:task:{task_id}`, TTL = 1 hour after terminal status.

**State transitions**:
```
submitted → working → completed
                   ↘ failed
                   ↘ timeout
                   ↘ input-required (order preview or HITL gate)
                          ↓ user confirms/modifies/cancels
                        working → completed
                               ↘ failed (user cancelled)
```

---

### A2AResult

Structured result returned by a Domain Agent upon task completion. Maps to Constitution requirement for `reasoning_summary` and `confidence`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `output` | `dict | str` | required | Domain-specific result payload |
| `reasoning_summary` | `str` | required | Brief explanation of decisions made |
| `confidence` | `float | null` | 0.0–1.0, optional | Agent's confidence in the result |
| `tool_calls` | `list[str]` | optional | Names of tools invoked during execution |

---

### OrderEntities

Extracted NLP entities from a raw Vietnamese order message.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `customer_name` | `str | null` | optional | Customer name (stripped of honorific) |
| `customer_honorific` | `str | null` | optional | Original honorific (anh/chị/etc.) |
| `table_number` | `str | null` | optional | Table identifier (e.g., "3", "A5") |
| `items` | `list[OrderItem]` | min 1, required | Extracted order items |
| `notes` | `str | null` | optional | Order-level notes |
| `intent_modifier` | `"new" | "add" | "remove" | "cancel"` | default "new" | Whether this modifies an existing order |

---

### OrderItem (extracted)

A single item extracted from natural language before product matching.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `product_query` | `str` | required | Raw product name from user input |
| `quantity` | `int` | min 1, default 1 | Quantity requested |
| `note` | `str | null` | optional | Item-level note (e.g., "ít đường", "thêm đá") |

---

### ProductMatch

Result of matching an `OrderItem.product_query` against the FAISS product index.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `product_query` | `str` | required | Original query |
| `matched_product_id` | `str | null` | optional | product ID if matched |
| `matched_product_name` | `str | null` | optional | Display name of matched product |
| `price` | `float | null` | optional | Unit price |
| `similarity_score` | `float` | 0.0–1.0, required | FAISS cosine similarity |
| `match_status` | `"auto" | "rerank" | "ask_user" | "not_found"` | required | Resolution path taken |
| `candidates` | `list[dict]` | optional | Top-3 candidates when status = "rerank" or "ask_user" |

**Thresholds**: ≥ 0.85 → `auto`; 0.65–0.84 → `rerank` (GPT-4o-mini selects from top-3); < 0.65 → `ask_user`

---

### OrderDraft

In-progress order held during multi-turn confirmation flow.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `draft_id` | `str` (UUID) | PK, required | Unique draft identifier (= A2A task_id) |
| `session_id` | `str` | FK → UserSession, required | Parent session |
| `customer_id` | `str | null` | optional | Resolved customer ID |
| `customer_name` | `str | null` | optional | Customer display name |
| `table_number` | `str | null` | optional | Table number |
| `items` | `list[ResolvedOrderItem]` | min 1, required | Items with resolved product IDs |
| `discount` | `float` | default 0.0 | Order-level discount |
| `notes` | `str | null` | optional | Order notes |
| `total_estimate` | `float | null` | optional | Calculated from items × prices |
| `status` | `"building" | "awaiting_confirm" | "confirmed" | "submitted" | "cancelled"` | required | Draft lifecycle |

**Storage**: Part of LangGraph OrderAgentState checkpoint, keyed by `thread_id = task_id`.

---

### ResolvedOrderItem

A matched and confirmed order item ready for submission.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `product_id` | `str` | required | product ID |
| `product_name` | `str` | required | Display name |
| `quantity` | `int` | min 1, required | Confirmed quantity |
| `price` | `float` | required | Unit price at time of order |
| `note` | `str | null` | optional | Item note |
| `variant` | `str | null` | optional | Product variant if applicable |

---

### DisplayPlan

User-visible representation of an Orchestrator plan, persisted to PostgreSQL. Decoupled from the internal routing plan (which lives in LangGraph state / Redis).

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `UUID` | PK, required | Unique plan identifier |
| `session_id` | `str` (max 128) | FK → UserSession, required | Parent session |
| `tenant_id` | `str` (max 128) | required | Tenant identifier |
| `user_message` | `str` | required | Original user message that triggered this plan |
| `goal` | `str` | required | Business-friendly goal in Vietnamese (e.g., "Xem doanh thu hôm nay và tạo đơn cho khách Lâm") |
| `status` | `"pending" \| "running" \| "completed" \| "failed"` | required, default `pending` | Plan lifecycle status |
| `created_at` | `datetime` (UTC) | required | Plan creation timestamp |
| `updated_at` | `datetime` (UTC) | required | Last status change timestamp |

**Storage**: PostgreSQL table `plans`. Indexes: `idx_plans_session(session_id, tenant_id)`.

**State transitions**:
```
pending → running (plan_service.create_plan sets status=running immediately)
       → completed (all PlanSubGoals reached terminal state successfully)
       → failed (any PlanSubGoal failed and no replan possible)
```

---

### PlanSubGoal

A single step within a DisplayPlan. Maps 1-to-1 with an A2A task once dispatched.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | `UUID` | PK, required | Unique sub-goal identifier |
| `plan_id` | `UUID` | FK → DisplayPlan ON DELETE CASCADE, required | Parent plan |
| `sequence` | `int` | required | Execution order (1-indexed) |
| `title` | `str` | required | Business-friendly step description in Vietnamese (e.g., "Truy vấn doanh thu tháng hiện tại") — NEVER contains agent/tool names |
| `agent_name` | `str` (max 100) | required | Internal agent identifier used for routing/debug (e.g., `"bi-agent"`) — NOT shown to end users |
| `agent_label` | `str` (max 100) | required | User-visible display name (e.g., `"Báo Cáo & Phân Tích"`) |
| `a2a_task_id` | `str` (max 128) | optional, nullable | A2A task ID once dispatched; NULL until link_task() is called |
| `status` | `"pending" \| "running" \| "completed" \| "failed"` | required, default `pending` | Sub-goal execution status |
| `result_summary` | `str \| null` | optional | Short human-readable result (e.g., "Doanh thu hôm nay: 8.4 triệu đồng") |
| `started_at` | `datetime (UTC) \| null` | optional | Set when link_task() is called |
| `completed_at` | `datetime (UTC) \| null` | optional | Set when terminal status is reached |

**Storage**: PostgreSQL table `plan_sub_goals`. Indexes: `idx_sub_goals_plan(plan_id, sequence)`, `idx_sub_goals_task(a2a_task_id)` for reverse lookup in `sync_from_task()`.

**Agent label mapping** (baked into plan_v1.md prompt):
| `agent_name` | `agent_label` |
|---|---|
| `order-agent` | `"Tạo & Quản Lý Đơn Hàng"` |
| `bi-agent` | `"Báo Cáo & Phân Tích"` |

**State transitions**:
```
pending → running  (link_task() called — A2A task dispatched)
       → completed (sync_from_task() receives "completed" status)
       → failed    (sync_from_task() receives "failed" status)
```

---

### BIQueryResult

Output of the BI Agent's query pipeline.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `query_id` | `str` (UUID) | PK, required | Unique query identifier |
| `nl_input` | `str` | required | Original Vietnamese question |
| `generated_sql` | `str` | required | SQL produced by NL2SQL node |
| `row_count` | `int` | required | Number of rows returned |
| `rows` | `list[dict]` | max 500, required | Raw result rows |
| `formatted_summary` | `str` | required | Human-readable answer |
| `limit_applied` | `bool` | required | Whether automatic LIMIT was injected |
| `safety_passed` | `bool` | required | Whether safety check passed |

---

## State Transitions Summary

### A2ATask Lifecycle
```
submitted → working → completed (success)
                   ↘ failed (error)
                   ↘ timeout (30s exceeded)
                   ↘ input-required (waiting for user)
                          ↓ (resume with user input)
                        working → completed
```

### OrderDraft Lifecycle
```
building (extracting + matching)
    ↓
awaiting_confirm (preview presented, interrupt active)
    ↓
confirmed (user approved)
    ↓
submitted (order sent to backend)
    OR
cancelled (user cancelled)
```

### ExecutionPlan Lifecycle
```
active → replanning (step failed, replan_count < max_replans)
      → completed (all steps done)
      → failed (max_replans exceeded or terminal error)
```

---

---

### AgentMemory

Persistent semantic fact accumulated by a Domain Agent over time. Upserted after each completed task via learning extraction.

**Storage**: PostgreSQL `agent_memory` table (permanent, no TTL)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | PK | Auto-generated |
| agent_name | VARCHAR(100) | ✓ | `"order-agent"` or `"bi-agent"` |
| tenant_id | VARCHAR(128) | ✓ | Tenant scope for multi-tenancy |
| memory_type | VARCHAR(50) | ✓ | Order: `product_alias` \| `customer_pref` \| `order_pattern` \| `vn_expression`; BI: `sql_pattern` \| `glossary_fix` \| `column_alias` \| `query_template` |
| key | TEXT | ✓ | Lookup key (alias text, customer_id, query intent, business term) |
| content | TEXT | ✓ | Fact content (the resolved value, preference, pattern, etc.) |
| confidence | FLOAT | ✓ | 0.0–1.0; default 0.8; UPSERT takes `GREATEST(existing, new)` |
| usage_count | INT | ✓ | Incremented each time the fact is retrieved; default 0 |
| last_used_at | TIMESTAMPTZ | | Updated on each retrieval |
| created_at | TIMESTAMPTZ | ✓ | Auto-set on insert |
| updated_at | TIMESTAMPTZ | ✓ | Auto-updated on upsert |

**Unique constraint**: `(agent_name, tenant_id, memory_type, key)` — enables idempotent upsert.

**Retrieval ordering**: `confidence DESC, usage_count DESC, last_used_at DESC` — most reliable and frequently used facts surface first.

**Examples**:

| agent_name | memory_type | key | content |
|---|---|---|---|
| order-agent | product_alias | "ba đen" | "cafe đen đá size L" |
| order-agent | customer_pref | "cust_123" | "Thường order trứng lộn x2 vào buổi sáng" |
| order-agent | vn_expression | "thêm vào" | "Intent: add_to_existing_order" |
| bi-agent | sql_pattern | "doanh thu theo ngày" | "SELECT DATE(created_at), SUM(net_revenue) FROM orders GROUP BY 1" |
| bi-agent | glossary_fix | "doanh thu" | "= cột net_revenue (không phải gross_amount)" |

---

### AgentTaskHistory

Episodic record of a completed A2A task. Used to surface similar past tasks for the next run.

**Storage**: PostgreSQL `agent_task_history` table (permanent, no TTL)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | PK | Auto-generated |
| agent_name | VARCHAR(100) | ✓ | `"order-agent"` or `"bi-agent"` |
| tenant_id | VARCHAR(128) | ✓ | Tenant scope |
| plan_id | UUID | FK → plans.id | Links to the Orchestrator plan that triggered this task |
| skill | VARCHAR(100) | ✓ | `"create_order"` \| `"bi_query"` |
| input_summary | TEXT | ✓ | Sanitised summary of task input — **MUST NOT contain raw PII** (customer names, phone numbers) |
| outcome | VARCHAR(20) | ✓ | `"success"` \| `"failed"` \| `"cancelled"` |
| key_decisions | JSONB | | Dict of `{tool_name: arguments}` — the tool calls agent made during task |
| learnings | TEXT | | JSON array of facts extracted by GPT-4o-mini after task |
| duration_ms | INT | | Task wall-clock duration |
| created_at | TIMESTAMPTZ | ✓ | Auto-set on insert |

**PII constraint**: `input_summary` stores a sanitised representation (e.g. `"order for customer_id=cust_123: 2x product_id=P42"`) — never the raw message text.

**Retrieval**: `find_similar_tasks()` queries WHERE `outcome='success'` AND `input_summary ILIKE '%{keyword}%'` ORDER BY `created_at DESC`.

---

### OrchestratorMemory

Persistent routing fact accumulated by the Orchestrator at the meta-level. Distinct from `AgentMemory` which stores domain facts — this stores routing strategies, plan templates, and user patterns.

**Storage**: PostgreSQL `orchestrator_memory` table (migration 003, permanent, no TTL)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | PK | Auto-generated |
| tenant_id | VARCHAR(128) | ✓ | Tenant scope |
| memory_type | VARCHAR(50) | ✓ | `"routing_pattern"` \| `"plan_template"` \| `"user_pattern"` \| `"routing_fix"` |
| key | TEXT | ✓ | Lookup key (intent class, request pattern, behavior pattern, failure pattern) |
| content | TEXT | ✓ | Routing insight (agent combo + rationale, plan structure, correction, etc.) |
| confidence | FLOAT | ✓ | 0.0–1.0; default 0.8; UPSERT takes `GREATEST(existing, new)` |
| usage_count | INT | ✓ | Incremented on retrieval; default 0 |
| last_used_at | TIMESTAMPTZ | | Updated on each retrieval |
| created_at | TIMESTAMPTZ | ✓ | Auto-set on insert |
| updated_at | TIMESTAMPTZ | ✓ | Auto-updated on upsert |

**Unique constraint**: `(tenant_id, memory_type, key)` — enables idempotent upsert.

**Retrieval ordering**: `confidence DESC, usage_count DESC` — most reliable patterns surface first.

**Examples**:

| memory_type | key | content |
|---|---|---|
| routing_pattern | "order+bi_combined" | "Request vừa tạo đơn vừa xem BI → chạy BI trước rồi mới Order" |
| plan_template | "daily_revenue_check" | "sub_goals: [bi-agent: doanh thu theo ngày]" |
| user_pattern | "add_after_confirm" | "User thường thêm món sau khi xem preview → nên confirm lại sau khi thêm" |
| routing_fix | "order_agent_timeout" | "Order Agent timeout khi >5 món → split thành 2 request" |

**Episodic complement**: `find_similar_plans()` queries the existing `plans` + `plan_sub_goals` tables directly — no separate episodic table needed.

---

## PII Identification (Constitution §Data & Compliance)

| Entity | PII Fields | Handling |
|--------|-----------|---------|
| ConversationTurn | `content` (may contain customer names, phone numbers) | TTL-based expiry (30 min active, 1 hour completed); not persisted to long-term DB |
| OrderDraft | `customer_name`, `customer_id`, `items` | Stored in LangGraph checkpoint (Redis, 1 hour TTL); never written to analytics DB |
| A2ATask | `params` (may contain customer info) | 1 hour TTL; not exported to analytics |
| ExecutionPlan | `goal`, `steps.params` | 1 hour TTL; audit logs retained 90 days per Constitution §Data |
| DisplayPlan | `user_message`, `goal` | PostgreSQL; does not store PII beyond original message — apply 90-day retention per Constitution default |
| PlanSubGoal | `title`, `result_summary` | PostgreSQL; may contain derived business data (revenue figures) — no direct PII; 90-day retention |
| AgentMemory | `key`, `content` (may contain customer_id but not raw name) | PostgreSQL permanent; `customer_pref` entries keyed by `customer_id` (opaque ID), not name — no direct PII |
| AgentTaskHistory | `input_summary` | PostgreSQL permanent; sanitised summary — raw PII MUST NOT be stored; 90-day retention policy applies |
| OrchestratorMemory | `key`, `content` (routing strategies, never customer data) | PostgreSQL permanent; stores meta-level routing patterns only — no customer PII by design |

**Regulation**: No specific regulation identified for MVP (internal tool). Apply 90-day audit log retention per Constitution default.

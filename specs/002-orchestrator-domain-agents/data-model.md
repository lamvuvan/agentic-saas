# Data Model: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Date**: 2026-03-16

---

## Entity Overview

```
UserSession ──────── has many ──────── ConversationTurn
     │
     └── has one active ─────────────── A2ATask
                                            │
                          ┌─────────────────┴──────────────────┐
                    (Order Agent)                         (BI Agent)
                       OrderDraft                       BIQueryResult
                          │
                    OrderEntities
                          │
                     ProductMatch (1..N)
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

One step within an ExecutionPlan.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `step_id` | `str` | required | Step identifier (e.g., "step-1") |
| `agent` | `"order-agent" | "bi-agent"` | required | Target Domain Agent |
| `skill` | `str` | required | A2A skill identifier (e.g., "create_order") |
| `params` | `dict[str, Any]` | required | Input parameters for the skill |
| `depends_on` | `list[str]` | default [] | `step_id`s that must complete first |
| `status` | `"pending" | "running" | "completed" | "failed"` | required | Step execution status |
| `result` | `dict | null` | optional | Result from Domain Agent when completed |
| `task_id` | `str | null` | optional | A2A task ID once dispatched |

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

**Storage**: Redis hash `a2a:task:{task_id}`, TTL = 1 hour after terminal status.

**State transitions**:
```
submitted → working → completed
                   ↘ failed
                   ↘ timeout
                   ↘ input-required → working (on resume)
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
| `matched_product_id` | `str | null` | optional | KiotViet product ID if matched |
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
| `customer_id` | `str | null` | optional | Resolved KiotViet customer ID |
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
| `product_id` | `str` | required | KiotViet product ID |
| `product_name` | `str` | required | Display name |
| `quantity` | `int` | min 1, required | Confirmed quantity |
| `price` | `float` | required | Unit price at time of order |
| `note` | `str | null` | optional | Item note |
| `variant` | `str | null` | optional | Product variant if applicable |

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
submitted (order sent to KiotViet)
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

## PII Identification (Constitution §Data & Compliance)

| Entity | PII Fields | Handling |
|--------|-----------|---------|
| ConversationTurn | `content` (may contain customer names, phone numbers) | TTL-based expiry (30 min active, 1 hour completed); not persisted to long-term DB |
| OrderDraft | `customer_name`, `customer_id`, `items` | Stored in LangGraph checkpoint (Redis, 1 hour TTL); never written to analytics DB |
| A2ATask | `params` (may contain customer info) | 1 hour TTL; not exported to analytics |
| ExecutionPlan | `goal`, `steps.params` | 1 hour TTL; audit logs retained 90 days per Constitution §Data |

**Regulation**: No specific regulation identified for MVP (internal tool). Apply 90-day audit log retention per Constitution default.

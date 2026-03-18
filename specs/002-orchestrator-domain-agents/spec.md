# Feature Specification: Orchestrator and Domain Agents

**Feature Branch**: `002-orchestrator-domain-agents`
**Created**: 2026-03-16
**Status**: Draft
**Input**: User description: "orchestrator and domain agent use Agentic_WorkPlan_v2.md"

## Overview

Staff at a retail/restaurant business interact with an AI assistant using natural language — including Vietnamese — to create orders and query business data. An Orchestrator receives every message, determines what the user wants, and routes the task to the appropriate specialist agent (Order Agent or BI Agent). Each specialist agent reasons through the task, calls the necessary tools, and returns a structured result. The Orchestrator assembles the final response for the user.

## User Scenarios & Testing

### User Story 1 — Intent Routing (Priority: P1)

A staff member sends a free-text message. The Orchestrator correctly identifies whether it is an order request, a business intelligence query, or casual conversation, and routes it to the right specialist agent without the user having to specify what kind of request it is.

**Why this priority**: All downstream value depends on correct routing. Without accurate intent classification, every other capability is unusable.

**Independent Test**: Send 20 diverse text messages covering order, BI, and conversational intents. Verify that at least 18/20 are classified correctly and routed to the matching agent stub (no real agent logic required).

**Acceptance Scenarios**:

1. **Given** a staff member sends "cho tôi xem doanh thu hôm nay", **When** the Orchestrator processes the message, **Then** the request is classified as a BI query and forwarded to the BI Agent.
2. **Given** a staff member sends "anh Lâm hai trứng lộn một cháo lòng", **When** the Orchestrator processes the message, **Then** the request is classified as an order and forwarded to the Order Agent.
3. **Given** a staff member sends "xin chào", **When** the Orchestrator processes the message, **Then** the system replies with a polite conversational response without routing to any agent.
4. **Given** a message that is ambiguous, **When** the Orchestrator cannot classify with sufficient confidence, **Then** it asks the user a single clarifying question rather than routing incorrectly.

---

### User Story 2 — Order Creation via Natural Language (Priority: P1)

A staff member describes an order in natural Vietnamese — including customer name, honorifics, table number, product names with quantities, and notes — and the Order Agent interprets all entities, matches products from the catalog, confirms the order with the user, and creates it.

**Why this priority**: Order creation is the primary business activity. Automating this via natural language is the core value proposition of the MVP.

**Independent Test**: Submit 20 natural language order inputs varying in complexity (single item, multi-item, table number, add-on notes, ambiguous product name). Verify that at least 17/20 produce a correct order preview with the right customer, products, quantities, and table.

**Acceptance Scenarios**:

1. **Given** the message "bàn 3 cho tôi 3 bò kho bánh mì", **When** the Order Agent processes it, **Then** it produces an order preview with table_number=3, items=[bò kho × 3, bánh mì × 3], and asks for confirmation.
2. **Given** the staff member confirms the preview, **When** the Order Agent receives the confirmation, **Then** it creates the order and returns the order reference number.
3. **Given** the customer name is not found in the system, **When** the Order Agent checks, **Then** it asks the staff member whether to create a new customer record before proceeding.
4. **Given** a product name is ambiguous (e.g., "cà phê"), **When** the Order Agent cannot confidently match it, **Then** it presents the top candidates and asks the user to choose.
5. **Given** an order is in the preview state, **When** the staff member says "thêm một trứng lộn", **Then** the Order Agent updates the preview without starting over.

---

### User Story 3 — Business Intelligence Queries (Priority: P1)

A staff member asks a business question in plain Vietnamese. The BI Agent interprets the question, retrieves the relevant data, and returns a clear, formatted answer — including summaries, tables, or key figures as appropriate.

**Why this priority**: Managers and owners need fast access to business data. Manual reporting delays decisions; real-time natural language queries eliminate this friction.

**Independent Test**: Submit 15 BI queries covering revenue, customer ranking, debt, inventory, and time-range grouping. Verify at least 12/15 produce correct, well-formatted answers.

**Acceptance Scenarios**:

1. **Given** the query "doanh thu hôm nay", **When** the BI Agent processes it, **Then** it returns today's total revenue as a number with currency formatting.
2. **Given** the query "top 5 khách hàng mua nhiều nhất tháng này", **When** the BI Agent processes it, **Then** it returns a ranked list of 5 customers with purchase totals.
3. **Given** the query "danh sách khách hàng còn công nợ trên 1 triệu", **When** the BI Agent processes it, **Then** it returns a list of customers meeting the debt threshold.
4. **Given** a query that would require data modification, **When** the BI Agent receives it, **Then** it rejects the query with a clear message and does not execute it.

---

### User Story 4 — Multi-Turn Conversation State (Priority: P2)

The system maintains conversation context across multiple turns within a session. A staff member can refer to previous messages, modify in-progress orders, or ask follow-up questions without re-stating full context.

**Why this priority**: Order creation always requires at least 2 turns (describe → confirm). Without conversation memory the system cannot function for its primary use case.

**Independent Test**: Run a 3-turn order conversation (describe order → add item → confirm). Verify the final order contains all items from all turns without duplication or loss.

**Acceptance Scenarios**:

1. **Given** the user already described an order, **When** the user says "thêm thêm một chai nước", **Then** the agent adds the item to the existing preview, not a new one.
2. **Given** a session with 3 prior turns, **When** a new message arrives, **Then** the agent's reasoning accounts for the recent context.
3. **Given** a session is idle for more than 30 minutes, **When** the user sends a new message, **Then** a new session starts cleanly without residual state.

---

### User Story 5 — Structured A2A Task Delegation (Priority: P1)

The Orchestrator delegates tasks to Domain Agents using a standardized asynchronous protocol. Domain Agents receive a well-defined task, execute it, and report completion. The Orchestrator polls for results and assembles the final response.

**Why this priority**: This is the communication backbone. Without A2A, the Orchestrator cannot use any Domain Agent — the entire multi-agent architecture depends on it.

**Independent Test**: Submit a task to the Order Agent's A2A endpoint and poll for completion. Verify the task transitions through `submitted` → `working` → `completed` and returns a valid result.

**Acceptance Scenarios**:

1. **Given** a valid A2A task submission, **When** the Domain Agent receives it, **Then** it returns a task ID immediately and begins processing asynchronously.
2. **Given** a task ID from a submitted task, **When** the Orchestrator polls the status endpoint, **Then** it receives one of: `submitted`, `working`, or `completed` with the result.
3. **Given** a Domain Agent task exceeding 30 seconds, **When** the Orchestrator polls, **Then** it receives a `timeout` status and the Orchestrator returns a user-friendly error message.
4. **Given** a Bearer token in the original request, **When** the Orchestrator forwards the task, **Then** the token is present in the A2A request header and is not stored anywhere.

---

### User Story 7 — Plan Visibility (Priority: P2)

A staff member can see what the system is doing in real-time while it processes their request. The system exposes a business-friendly plan with a Vietnamese goal description and a checklist of sub-goals, each showing whether it is pending, running, or completed.

**Why this priority**: Requests involving multiple agents (order + BI) can take several seconds. Without progress visibility, users have no feedback and may assume the system is broken.

**Independent Test**: Submit a request, then poll `GET /plans/{session_id}/current` — verify the plan appears within 200ms of the response and that sub-goal status transitions from `pending` → `running` → `completed` as A2A tasks progress.

**Acceptance Scenarios**:

1. **Given** a request is being processed, **When** the frontend polls `GET /plans/{session_id}/current`, **Then** it receives the plan's goal text in Vietnamese and a list of sub-goals with their current status.
2. **Given** a sub-goal is dispatched to a Domain Agent, **When** the A2A task status changes to `completed`, **Then** the sub-goal status is updated to `completed` and includes a `result_summary`.
3. **Given** all sub-goals are completed, **When** the polling endpoint is called, **Then** the parent plan status is `completed`.
4. **Given** an A2A task fails, **When** the polling endpoint is called, **Then** the affected sub-goal shows `failed` status — the plan does not silently remain `running`.
5. **Given** the frontend requests `GET /plans/{plan_id}`, **Then** the response does not expose internal agent names or technical parameters to the user — only the `agent_label` is returned.

---

### User Story 8 — Domain Agent Memory (Priority: P2)

A Domain Agent learns from past tasks and accumulated domain knowledge. Over time it recognises returning customers' preferences, resolves ambiguous product names using previously-confirmed aliases, and avoids SQL mistakes it has corrected before — without the user needing to repeat context they've already provided.

**Why this priority**: Repeated friction (re-confirming the same alias, same customer pref, same SQL fix) erodes trust. Memory turns one-off corrections into permanent improvements that compound in value.

**Independent Test**: Submit the same order twice using an alias ("ba đen"). After the first task the alias is stored. On the second task the agent must resolve "ba đen" → "cafe đen đá size L" from memory without prompting the user.

**Acceptance Scenarios**:

1. **Given** an Order Agent task that resolves "ba đen" to "cafe đen đá size L", **When** the task completes, **Then** a `product_alias` memory entry is stored linking "ba đen" → "cafe đen đá size L" with confidence ≥ 0.8.
2. **Given** a returning customer "Lâm" who previously ordered "trứng lộn x2", **When** the Order Agent processes a new order for that customer, **Then** the customer preference is injected into the system prompt so the agent may anticipate it.
3. **Given** the BI Agent previously corrected "doanh thu" → `net_revenue` column, **When** a similar query arrives, **Then** the correction is loaded from semantic memory and applied before generating SQL.
4. **Given** a completed A2A task, **When** learning extraction runs, **Then** ≤ 3 facts are upserted into `agent_memory` and a row is inserted into `agent_task_history` — the task is not blocked if extraction fails.
5. **Given** `POSTGRES_DSN` is not configured, **When** a Domain Agent starts, **Then** memory features degrade gracefully (no DB calls) and normal task processing continues unaffected.

---

### User Story 9 — Orchestrator Memory (Priority: P2)

The Orchestrator learns from past routing decisions and plan outcomes. Over time it recognises which agent combinations work best for specific request types, reuses successful plan structures, and avoids routing strategies that previously failed — without the user needing to see any of this meta-learning.

**Why this priority**: Routing errors (sending an order+BI combined request to a single agent) degrade UX and waste tokens. Memory at the Orchestrator level turns one-off corrections into permanent routing improvements that compound across all tenants.

**Independent Test**: Submit a combined order+BI request, complete it successfully. After completion, trigger learning extraction. On the next similar request, verify the routing pattern is loaded into the plan prompt (appears in rendered system prompt context).

**Acceptance Scenarios**:

1. **Given** a completed plan with `status=completed`, **When** `extract_and_store()` runs, **Then** ≤ 3 routing insights are upserted into `orchestrator_memory` — the plan is not blocked if extraction fails.
2. **Given** a tenant with existing routing patterns, **When** the Plan node generates a new plan, **Then** relevant `routing_pattern` and `similar_plans` entries appear in the system prompt passed to GPT-4o.
3. **Given** a routing fix was previously stored ("order+bi → run BI first"), **When** a similar combined request arrives, **Then** the routing_fix context is injected and the Plan node can apply it.
4. **Given** `POSTGRES_DSN` is not configured, **When** the Orchestrator processes a request, **Then** memory retrieval and storage are silently skipped — planning continues without degradation.
5. **Given** a `routing_fix` entry already stored with confidence 0.7, **When** a new extraction produces the same key with confidence 0.6, **Then** the stored confidence remains 0.7 (GREATEST semantics).

---

### User Story 10 — HITL Confirmation for Mutating Tools (Priority: P1)

Before a Domain Agent executes any tool that modifies data (creates order, updates order, cancels order, creates customer), it must pause the ReAct loop, generate a clear Vietnamese confirmation message describing the action and its impact, and wait for explicit staff approval. The agent resumes only after the user confirms, or gracefully handles modifications, cancellations, and scope changes.

**Why this priority**: Mutating operations are irreversible or difficult to reverse. An agent executing `order__create_order` or `order__cancel_order` without human approval exposes the business to errors and loss of trust. HITL is the safety gate for all write operations.

**Independent Test**: Trigger an Order Agent task that calls `order__create_order`. Verify that: (1) the A2A task transitions to `input_required` before the tool executes; (2) the confirmation message contains the order details and impact in Vietnamese; (3) after the user confirms, the tool executes and the task transitions to `completed`.

**Acceptance Scenarios**:

1. **Given** the Order Agent is about to call `order__create_order`, **When** the tool is detected as mutating (`requires_confirmation: true`), **Then** the ReAct loop pauses, the A2A task status becomes `input_required`, and the user receives a Vietnamese confirmation message stating the order details and total amount.
2. **Given** the user responds "xác nhận" or equivalent, **When** `resume_after_hitl()` classifies the intent as `confirm`, **Then** the tool executes with the original arguments and the ReAct loop continues to completion.
3. **Given** the user responds with a modification (e.g., "giảm xuống 2 ly"), **When** `resume_after_hitl()` classifies the intent as `modify`, **Then** the user response is injected into the message context and the ReAct loop re-reasons with the updated intent — no explicit replan needed.
4. **Given** the user responds "thôi bỏ đi" or equivalent, **When** `resume_after_hitl()` classifies the intent as `cancel`, **Then** a cancellation message is injected and the ReAct loop re-reasons to acknowledge cancellation — the mutating tool is NOT called.
5. **Given** a read-only tool (`requires_confirmation: false`) such as `customer__get_customers`, **When** the agent calls it, **Then** it executes immediately without any confirmation pause.
6. **Given** `impact_template` is defined in `tools.yaml` for `order__cancel_order`, **When** the confirmation message is generated, **Then** the message includes the order ID, item count, and total amount — never a generic "confirm?" without context.

---

### User Story 11 — Parallel & Sequential Task Dispatch (Priority: P1)

The Orchestrator dispatches tasks to Domain Agents in parallel when steps have no dependencies, and in sequence when one step's output is required by another. Each Domain Agent receives a rich `A2ATaskPayload` that includes the original user message, per-step instructions, conversation history, and the results of any upstream steps — so downstream agents have full context without the user repeating anything.

**Why this priority**: Combined order+BI requests ("đặt 3 bò kho và cho tôi xem doanh thu hôm nay") can serve both agents simultaneously. Sequential dispatch with dependency injection enables agents to build on each other's work — the Order Agent can reference a customer record created by a prior step. Without this, multi-agent plans are either slow (forced serial) or context-blind (agents can't reference upstream results).

**Independent Test**: Submit a request that produces a plan with two independent steps (order + BI). Verify both A2A tasks are submitted within 200ms of each other (parallel dispatch). Then submit a request where step 2 `depends_on` step 1 — verify step 2's `A2ATaskPayload.dependency_results` contains step 1's output.

**Acceptance Scenarios**:

1. **Given** a plan with two steps both having `depends_on: []`, **When** the DispatchEngine executes, **Then** both A2A tasks are submitted concurrently via `asyncio.gather` — not serially.
2. **Given** a plan where step 2 `depends_on: ["order-agent"]`, **When** step 1 (order-agent) completes, **Then** step 2's `A2ATaskPayload.dependency_results` contains `{"order-agent": <step1_result>}`.
3. **Given** an upstream step fails, **When** a downstream step `depends_on` it, **Then** the downstream step is not dispatched and its sub-goal status is set to `failed`.
4. **Given** a Domain Agent receives an `A2ATaskPayload`, **When** it builds its system prompt via `build_agent_system_prompt()`, **Then** the prompt includes the `instructions` field as the task goal, the `original_message` for context, and any `dependency_results` as prior step context.
5. **Given** an `A2ATaskPayload` with `conversation_history` (last 6 turns), **When** the Domain Agent builds its system prompt, **Then** recent conversation turns are included so the agent can reference prior context without requiring the Orchestrator to re-state it.

---

### User Story 6 — Voice Input (Priority: P2)

A staff member speaks a request instead of typing. The system transcribes the audio and processes it identically to a typed message, routing to the same agents with the same quality.

**Why this priority**: In a busy restaurant or retail environment, typing is impractical. Voice is the natural input mode for floor staff.

**Independent Test**: Submit 10 Vietnamese audio clips covering order and BI requests. Verify that at least 9/10 transcriptions have fewer than 15% word errors, and each routes correctly.

**Acceptance Scenarios**:

1. **Given** an audio clip of "anh Lâm hai trứng lộn", **When** the system processes it, **Then** the Order Agent receives the correct transcription and creates the right preview.
2. **Given** audio with background noise that cannot be understood, **When** the system processes it, **Then** it responds with a clear "please repeat" message — never a silent failure.

---

### Edge Cases

- What happens when all Domain Agents are unavailable? → Orchestrator returns a user-friendly degraded message, not a raw error.
- What happens when a product name matches nothing in the catalog? → Order Agent asks the user to describe differently or choose from similar items.
- What happens when a BI query would return more than 500 rows? → BI Agent applies an automatic limit and notes this in the response.
- What happens when the AI reasoning times out mid-task? → The agent returns a user-friendly message; the failure is logged for observability.
- What happens when a customer has multiple name matches? → Order Agent presents the top matches and asks the user to confirm which one.
- What happens when a staff member cancels an in-progress order preview? → Order Agent discards the draft and acknowledges the cancellation.
- What happens when the HITL classification cannot determine the user's intent? → System defaults to `cancel` to avoid unintended data mutation; user is informed and can retry.
- What happens when a voice clip is too long or too noisy? → System responds with a clear "could not understand" message — no silent failure.
- What happens when the same session ID is used concurrently from two devices? → Each request is processed in turn; state reflects the last confirmed action.

---

## Requirements

### Functional Requirements

**Orchestrator**

- **FR-001**: The Orchestrator MUST accept user messages via an HTTP text endpoint and return a response.
- **FR-002**: The Orchestrator MUST classify each message into one of: `order`, `bi_query`, `chitchat`, or `unknown`, with accuracy ≥ 90% on standard test inputs.
- **FR-003**: The Orchestrator MUST create a serializable execution plan before delegating tasks to Domain Agents — the plan must be observable in structured logs.
- **FR-004**: The Orchestrator MUST delegate tasks to the appropriate Domain Agent via the A2A protocol, forwarding the caller's Bearer token.
- **FR-005**: The Orchestrator MUST aggregate Domain Agent responses and return a single coherent reply to the user.
- **FR-006**: The Orchestrator MUST maintain per-session conversation history covering at least the last 3 turns.
- **FR-007**: The Orchestrator MUST accept voice input (audio file) and convert it to text before intent classification.
- **FR-008**: The Bearer token MUST be forwarded through the entire request chain and MUST NOT appear in any log, database record, or response body.

**Order Domain Agent**

- **FR-009**: The Order Agent MUST extract order entities (customer name, honorifics, table number, product names, quantities, notes) from Vietnamese natural language input.
- **FR-010**: The Order Agent MUST match extracted product names against the active product catalog and resolve ambiguous names by presenting top candidates to the user.
- **FR-011**: The Order Agent MUST look up or create a customer record before submitting an order — it MUST NOT submit an order without a confirmed customer identity.
- **FR-012**: The Order Agent MUST present an order preview and wait for explicit staff confirmation before submitting the order to the backend system.
- **FR-013**: The Order Agent MUST support multi-turn modification — a staff member must be able to add, remove, or change items in a preview before confirming.
- **FR-014**: The Order Agent MUST handle Vietnamese number words (một=1, hai=2 … mười=10, mười hai=12) and common honorifics (anh, chị, em, bác, cô, chú, ông, bà).

**BI Domain Agent**

- **FR-015**: The BI Agent MUST translate Vietnamese natural language questions into structured queries and execute them against the analytics data store.
- **FR-016**: The BI Agent MUST only execute read-only queries — any attempt to modify data MUST be rejected with a clear error message.
- **FR-017**: The BI Agent MUST automatically apply a maximum row limit if the query does not include one.
- **FR-018**: The BI Agent MUST format results in a human-readable response (summary text, table, or key figures appropriate to the query type).

**A2A Protocol (both Domain Agents)**

- **FR-019**: Domain Agents MUST expose a task submission endpoint that accepts a task and returns a task ID immediately.
- **FR-020**: Domain Agents MUST expose a task status endpoint that returns current status and result when complete.
- **FR-021**: Task status MUST follow the lifecycle: `submitted` → `working` → `completed` (or `failed` / `timeout`).

**Plan Persistence & Display**

- **FR-022**: The Orchestrator MUST generate a dual-layer plan in a single LLM call: a `display` layer (Vietnamese business language goal + sub-goals with agent labels) and a `routing` layer (internal technical steps for A2A dispatch).
- **FR-023**: The Orchestrator MUST persist the display plan to PostgreSQL immediately after the Plan node completes — before any A2A dispatch begins. The `plans` row MUST be inserted with `status=running` and all `plan_sub_goals` rows inserted in the same operation.
- **FR-024**: The Orchestrator MUST expose `GET /plans/{session_id}/current` (latest plan for session) and `GET /plans/{plan_id}` (plan detail) endpoints.
- **FR-025**: Sub-goal status MUST be automatically synchronized with the A2A task status in the dispatch polling loop — no manual status management. When all sub-goals reach a terminal state, the parent plan status MUST be automatically updated to `completed` or `failed`.
- **FR-026**: The `agent_label` field (e.g. "Tạo & Quản Lý Đơn Hàng") MUST be used in all user-visible responses; internal `agent_name` values (e.g. "order-agent") MUST NOT appear in API responses intended for end users.

**Domain Agent Memory**

- **FR-027**: Each Domain Agent MUST retrieve relevant semantic memories (facts, aliases, preferences, SQL patterns) before starting its reasoning loop, and inject them into the system prompt.
- **FR-028**: After each completed A2A task, a Domain Agent MUST call GPT-4o-mini to extract ≤ 3 useful facts and upsert them into `agent_memory`. This extraction is best-effort — failures MUST NOT block task completion.
- **FR-029**: After each A2A task, a Domain Agent MUST record a row in `agent_task_history` with `input_summary`, `outcome`, `key_decisions`, `learnings`, and `duration_ms`. Raw PII MUST NOT be stored in `input_summary`.
- **FR-030**: `MemoryService` MUST be a shared library (`shared/memory_service.py`) usable by both Order Agent and BI Agent without duplication.
- **FR-031**: When `POSTGRES_DSN` is not configured, memory retrieval and storage MUST be silently skipped — the agent MUST function normally without memory.

**Task Dispatch & Dependency Engine**

- **FR-043**: The Orchestrator MUST send an `A2ATaskPayload` to Domain Agents that includes four groups: Identity (`task_id`, `plan_id`, `sub_goal_sequence`, `session_id`, `tenant_id`), Intent (`original_message`, `skill`, `instructions` — a per-step goal string generated by the Plan LLM), Conversation context (`conversation_history`: last 6 turns from session), and Dependency results (`dependency_results`: `dict[str, dict]` mapping upstream agent name → result).
- **FR-044**: The `PlanStep` schema MUST include `depends_on: list[str]` (agent names this step waits for) and `instructions: str` (a plain-language description of this specific step's goal, generated by the Plan node LLM). Both fields MUST be populated for every step in the routing plan.
- **FR-045**: The `DispatchEngine` at `orchestrator/core/dispatch_engine.py` MUST execute steps in dependency order: steps with empty `depends_on` are dispatched in parallel via `asyncio.gather`; downstream steps are dispatched only after all their dependencies have completed and `dependency_results` is injected into their `A2ATaskPayload`. If an upstream step fails, all downstream steps that depend on it MUST be skipped with `failed` status.

**HITL — Human-in-the-Loop Confirmation**

- **FR-037**: Domain Agents MUST check `requires_confirmation` on each tool definition before execution. Tools with `requires_confirmation: false` (read-only) MUST execute immediately. Tools with `requires_confirmation: true` (mutating) MUST pause the ReAct loop and return a `__hitl__` signal.
- **FR-038**: When a mutating tool is detected, the agent MUST call GPT-4o-mini with `CONFIRM_PROMPT` and the tool's `impact_template` (from `tools.yaml`) to generate a concise Vietnamese confirmation message stating the action, its data impact, and a clear confirmation request.
- **FR-039**: The A2A task status MUST transition to `input_required` when HITL is triggered. The `input_request` field MUST contain the generated confirmation message. The ReAct loop MUST be suspended until `resume_after_hitl()` is called.
- **FR-040**: `resume_after_hitl()` MUST classify the user response into one of four intents: `confirm` | `modify` | `cancel` | `scope_change`. Each intent drives a distinct code path: confirm → execute tool and continue loop; modify → inject response and re-reason; cancel → inject cancellation and re-reason; scope_change → return `__scope_change__` signal to Orchestrator.
- **FR-041**: The `config/tools.yaml` tool definitions for all mutating tools MUST include `requires_confirmation: true` and an `impact_template` string describing the data change for confirmation message generation.
- **FR-042**: HITL response classification (`_classify_hitl_response`) MUST use GPT-4o-mini with a Vietnamese-aware prompt. The `confirm_message` task type MUST be routed to `fast` model in `shared/llm_client.py`.

**Orchestrator Memory**

- **FR-032**: The Orchestrator MUST persist routing patterns, plan templates, user patterns, and routing fixes to a `orchestrator_memory` table (migration 003) via `OrchestratorMemoryService`.
- **FR-033**: After each plan reaches `completed` status, the Orchestrator MUST call GPT-4o-mini to extract ≤ 3 routing insights and upsert them into `orchestrator_memory`. This extraction is best-effort — failures MUST NOT block any response.
- **FR-034**: The Plan node MUST retrieve relevant routing patterns and similar completed plans before calling GPT-4o, and inject them into the system prompt via `{routing_memory}` and `{similar_plans}` placeholders.
- **FR-035**: `OrchestratorMemoryService` MUST use GREATEST semantics on conflict — confidence is only updated if the new value exceeds the existing value.
- **FR-036**: When `POSTGRES_DSN` is not configured, `OrchestratorMemoryService` MUST be `None` and all memory calls in the Plan node MUST be silently skipped.

### Key Entities

- **UserSession**: A conversation context keyed by session ID, holding message history (last N turns), current in-progress task state, and session expiry timestamp.
- **IntentClassification**: The result of classifying a message — intent type, confidence score, and any extracted top-level entities.
- **ExecutionPlan**: A serializable ordered list of steps the Orchestrator will take — which agent, skill, and parameters — created before any delegation.
- **A2ATask**: An asynchronous work unit submitted to a Domain Agent — task ID, skill name, input parameters, status, result payload, and timestamps.
- **DisplayPlan**: The user-visible representation of a plan persisted in PostgreSQL — plan ID, session ID, goal text (Vietnamese), status (`pending` | `running` | `completed` | `failed`), and a list of sub-goals. Decoupled from the internal routing plan.
- **PlanSubGoal**: A single step within a DisplayPlan — sequence number, title (Vietnamese business language), `agent_name` (internal, for routing/debug), `agent_label` (user-visible display name), `a2a_task_id` (nullable until dispatched), status, `result_summary`, and timestamps.
- **OrderDraft**: An intermediate order held during multi-turn confirmation — customer identity, resolved items (with product IDs and prices), table number, discount, and notes.
- **OrderEntities**: NLP extraction output from a raw order message — customer name, honorific, table number, and a list of (product query, quantity, note) tuples.
- **BIQueryResult**: BI Agent output — the generated query, rows returned (up to the limit), row count, and a formatted human-readable summary.
- **AgentMemory**: A persistent semantic fact stored per agent and tenant — `memory_type` (`product_alias` | `customer_pref` | `order_pattern` | `vn_expression` | `sql_pattern` | `glossary_fix` | `column_alias` | `query_template`), lookup `key`, `content`, `confidence`, `usage_count`. Upserted after each task; updated on retrieval.
- **AgentTaskHistory**: An episodic record of a completed A2A task — `agent_name`, `tenant_id`, `plan_id` (FK → plans), `skill`, `input_summary` (no raw PII), `outcome` (`success` | `failed` | `cancelled`), `key_decisions` (JSONB), `learnings`, `duration_ms`. Used by `find_similar_tasks()` to surface past patterns.
- **HITLConfirmation**: A transient confirmation state created when a mutating tool is intercepted — `tool_name`, `pending_args`, `confirmation_message` (Vietnamese), `intent` (after user responds: `confirm` | `modify` | `cancel` | `scope_change`). Not persisted; held in ReAct loop context until resolved.
- **A2ATaskPayload**: The enriched task payload sent from the Orchestrator to a Domain Agent — four groups: (1) Identity: `task_id`, `plan_id`, `sub_goal_sequence`, `session_id`, `tenant_id`; (2) Intent: `original_message`, `skill`, `instructions` (per-step goal from Plan LLM); (3) Conversation: `conversation_history` (last 6 turns); (4) Dependencies: `dependency_results` (`dict[str, dict]` — upstream agent name → its result). The Domain Agent's `build_agent_system_prompt(payload, memory_context)` assembles these groups into the LLM system prompt.
- **OrchestratorMemory**: A persistent routing fact stored per tenant — `memory_type` (`routing_pattern` | `plan_template` | `user_pattern` | `routing_fix`), lookup `key`, `content`, `confidence`, `usage_count`. GREATEST semantics on upsert. Used by `OrchestratorMemoryService` to inject routing context into the Plan node system prompt before GPT-4o is called.

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: Intent classification accuracy ≥ 90% on a 20-case evaluation suite covering order, BI, and chitchat intents.
- **SC-002**: Order entity extraction precision and recall ≥ 90% on 20 Vietnamese natural language inputs.
- **SC-003**: Product name matching recall@1 ≥ 85% on 30 product name variation test cases.
- **SC-004**: BI query correctness ≥ 80% on 20 representative queries (revenue, customer ranking, debt, inventory, time ranges).
- **SC-005**: End-to-end order creation success rate ≥ 85% on 20 full-conversation test cases (from first message to confirmed order).
- **SC-006**: End-to-end BI query success rate ≥ 80% on 20 BI query test cases.
- **SC-007**: P95 response time for order conversations ≤ 3 seconds from message received to reply returned.
- **SC-008**: P95 response time for BI queries ≤ 5 seconds from message received to reply returned.
- **SC-009**: Voice transcription Word Error Rate < 15% on 10 Vietnamese audio test clips.
- **SC-010**: Zero occurrences of any Bearer token appearing in any log, database, or API response body across all services.
- **SC-011**: A2A task lifecycle (`submitted` → `working` → `completed`) executes correctly in 100% of test runs.
- **SC-012**: Six live demo scenarios (2 order chat, 1 order voice, 1 BI revenue, 1 BI customer ranking, 1 BI debt) complete without unhandled errors.
- **SC-013**: `GET /plans/{session_id}/current` returns a plan with correct sub-goal statuses within 200ms of A2A task status change on 100% of polling requests.
- **SC-014**: Plan is persisted to PostgreSQL within 200ms of Plan node completion across all end-to-end test cases.
- **SC-015**: After a product alias is confirmed in one Order Agent task, a second task using that alias resolves without user confirmation in 100% of test runs.
- **SC-016**: Learning extraction (GPT-4o-mini) completes within 2 seconds after task completion and does not increase overall task P95 latency by more than 2 seconds.
- **SC-017**: Zero PII (customer names, phone numbers) stored in `agent_task_history.input_summary` across all test cases.
- **SC-018**: After a plan completes, routing insight extraction finishes within 2 seconds and does not increase plan P95 latency by more than 2 seconds.
- **SC-019**: On the second identical combined request type, the Plan node system prompt contains the matching `routing_pattern` entry from `orchestrator_memory` in 100% of test runs.
- **SC-020**: Every mutating tool call (`requires_confirmation: true`) pauses the ReAct loop and transitions the A2A task to `input_required` in 100% of test runs — no mutating tool executes without prior user confirmation.
- **SC-021**: HITL confirmation message contains tool name, action description, and data impact in Vietnamese in 100% of test runs. Classification latency (GPT-4o-mini) ≤ 500ms added to total task latency.
- **SC-022**: When an Orchestrator plan has two independent steps (both `depends_on: []`), both A2A tasks are submitted within 200ms of each other (parallel dispatch) in 100% of test runs.
- **SC-023**: When a plan step has `depends_on` set, the downstream Domain Agent's `A2ATaskPayload.dependency_results` contains the upstream agent's result in 100% of test runs.

---

## Assumptions

- The Tool Registry (feature 001) is deployed and operational; Orchestrator and Domain Agents consume it.
- The product catalog can be loaded at agent startup and refreshed periodically (assumed every 30 minutes).
- All services use the same Bearer token forwarding approach established in feature 001.
- Vietnamese is the primary language for user inputs; English is used for error messages and internal logs.
- Session inactivity timeout is 30 minutes; history is discarded after expiry.
- Confidence thresholds for product matching: ≥ 85% → auto-select, 65–84% → present top candidates for confirmation, < 65% → ask user to re-describe.
- The order confirmation step is mandatory — no order is submitted without explicit staff approval.
- Voice input in MVP is audio file upload (not real-time streaming).

---

## Out of Scope (v1)

- Real-time streaming WebSocket chat responses
- Inventory Agent, Campaign Agent, or any agent beyond Order and BI
- Tool Registry v2 (database-backed) — v1 YAML config only
- Monitoring dashboard or alerting
- Multi-language support beyond Vietnamese input and English error messages

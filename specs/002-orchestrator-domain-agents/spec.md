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

### Key Entities

- **UserSession**: A conversation context keyed by session ID, holding message history (last N turns), current in-progress task state, and session expiry timestamp.
- **IntentClassification**: The result of classifying a message — intent type, confidence score, and any extracted top-level entities.
- **ExecutionPlan**: A serializable ordered list of steps the Orchestrator will take — which agent, skill, and parameters — created before any delegation.
- **A2ATask**: An asynchronous work unit submitted to a Domain Agent — task ID, skill name, input parameters, status, result payload, and timestamps.
- **OrderDraft**: An intermediate order held during multi-turn confirmation — customer identity, resolved items (with product IDs and prices), table number, discount, and notes.
- **OrderEntities**: NLP extraction output from a raw order message — customer name, honorific, table number, and a list of (product query, quantity, note) tuples.
- **BIQueryResult**: BI Agent output — the generated query, rows returned (up to the limit), row count, and a formatted human-readable summary.

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
- Parallel multi-agent task execution (sequential delegation only in MVP)
- Inventory Agent, Campaign Agent, or any agent beyond Order and BI
- Tool Registry v2 (database-backed) — v1 YAML config only
- Human-in-the-loop (HITL) approval gateway
- Monitoring dashboard or alerting
- Multi-language support beyond Vietnamese input and English error messages

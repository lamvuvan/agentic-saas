# Tasks: Orchestrator and Domain Agents

**Input**: Design documents from `/specs/002-orchestrator-domain-agents/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US5)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Service directories, dependency declarations, Docker Compose additions, environment template

- [x] T001 Create service directories: orchestrator/, order_agent/, bi_agent/, shared/a2a/, tests/002-orchestrator-domain-agents/{contract,integration,unit}/
- [x] T002 [P] Add Python dependencies to pyproject.toml or requirements files: langgraph>=0.2, langgraph-checkpoint-redis>=0.0.6, sentence-transformers, faiss-cpu, asyncpg, langchain-openai, httpx, respx
- [x] T003 [P] Add orchestrator, order-agent, bi-agent services to docker-compose.yml with ports 8000/8002/8003, healthcheck endpoints, and Redis/Postgres dependencies
- [x] T004 [P] Add new environment variables to .env.example: ORDER_AGENT_URL, BI_AGENT_URL, OPENAI_MODEL_FAST (gpt-4o-mini), OPENAI_MODEL_SMART (gpt-4o), A2A_POLL_INTERVAL_MS (500), A2A_TIMEOUT_MS (30000)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared A2A models, LLM client, three FastAPI app factories with Agent Cards, test conftest

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete

- [x] T005 Create shared/a2a/__init__.py and shared/a2a/models.py with Pydantic v2 models: TaskStatus (enum), A2ATask, A2AResult (output, reasoning_summary, confidence, tool_calls), AgentCard, ExecutionPlan, PlanStep
- [x] T006 [P] Create shared/llm_client.py with select_model(task_type) routing table (see research.md Decision 5), chat_completion_async() with json_schema Structured Outputs, 3-attempt exponential backoff retry, prompt_tokens/completion_tokens structured log fields
- [x] T007 [P] Create shared/a2a/server.py with Redis-backed task store helpers: store_task(), get_task(), update_task_status() using redis.asyncio, key pattern a2a:task:{task_id}, 1-hour TTL on terminal states
- [x] T008 [P] Create shared/a2a/client.py with submit_task(agent_url, skill, params, token) and poll_task(agent_url, task_id) coroutines using httpx
- [x] T009 Create orchestrator/main.py: FastAPI app factory with lifespan, GET /.well-known/agent.json (Orchestrator Agent Card per contracts/agent-card.json), GET /health endpoint
- [x] T010 [P] Create order_agent/main.py: FastAPI app factory with lifespan, GET /.well-known/agent.json (Order Agent Card, role: domain, domain: order), GET /health endpoint
- [x] T011 [P] Create bi_agent/main.py: FastAPI app factory with lifespan, GET /.well-known/agent.json (BI Agent Card, role: domain, domain: bi), GET /health endpoint
- [x] T012 Create tests/002-orchestrator-domain-agents/conftest.py with autouse pytest fixture that calls set_auth("", "") before and after each test to reset auth ContextVar state

**Checkpoint**: Foundation ready — all 3 app factories exist, shared A2A models defined, conftest resets auth state between tests

---

## Phase 3: User Story 5 — Structured A2A Task Delegation (Priority: P1) 🎯 Backbone

**Goal**: Domain Agents accept tasks via POST /a2a/tasks and return structured results via GET /a2a/tasks/{id}. Orchestrator can submit, poll, and handle timeout. This is the communication backbone all other stories depend on.

**Independent Test**: Submit a task directly to Order Agent A2A endpoint, poll until completed/input-required. Verify task_id returned immediately, status transitions from submitted→working, Authorization header forwarded and not stored.

### Contract & Integration Tests (write first — must FAIL before implementation)

- [x] T013 [P] [US5] Write contract tests for A2A task submit (202 + task_id), poll (200 + lifecycle statuses), and 404 on unknown task_id in tests/002-orchestrator-domain-agents/contract/test_a2a_contract.py
- [x] T014 [P] [US5] Write integration test verifying Bearer token forwarded from Orchestrator through to Domain Agent A2A header (not in logs, not in task record) in tests/002-orchestrator-domain-agents/integration/test_token_forwarding.py
- [x] T015 [P] [US5] Write integration test for full A2A task lifecycle (submitted→working→completed) against Order Agent stub in tests/002-orchestrator-domain-agents/integration/test_a2a_lifecycle.py

### Implementation

- [x] T016 [US5] Implement order_agent/a2a_server.py: POST /a2a/tasks (accept task, store with status=submitted, launch asyncio.create_task for processing, return 202 + task_id), GET /a2a/tasks/{task_id} (fetch from Redis task store, return current status + result)
- [x] T017 [US5] Implement bi_agent/a2a_server.py: POST /a2a/tasks, GET /a2a/tasks/{task_id} — identical pattern to order_agent/a2a_server.py, wired to BI Agent graph stub
- [x] T018 [US5] Implement orchestrator/a2a_client.py: submit_to_agent(agent_url, skill, params) → task_id, poll_for_result(agent_url, task_id) with 500ms interval and 30s total timeout, return A2ATask or raise TimeoutError on 30s exceeded
- [x] T019 [US5] Mount A2A routers from order_agent/a2a_server.py and bi_agent/a2a_server.py into their respective main.py apps

**Checkpoint**: A2A protocol fully functional — Domain Agent A2A endpoints accept tasks, return status, Orchestrator client can submit + poll + timeout

---

## Phase 4: User Story 1 — Intent Routing (Priority: P1) 🎯 MVP

**Goal**: POST /chat classifies Vietnamese messages into order/bi_query/chitchat/unknown and routes to the correct Domain Agent via A2A. Chitchat handled directly.

**Independent Test**: Send 20 diverse messages via POST /chat to agent stubs. Verify ≥ 18/20 correctly classified, response contains session_id + reply + intent + trace_id.

### Contract & Integration Tests (write first — must FAIL before implementation)

- [x] T020 [P] [US1] Write contract tests for POST /chat response schema (200 required fields, 422 validation error, 504 AGENT_TIMEOUT) against contracts/chat.json in tests/002-orchestrator-domain-agents/contract/test_chat_contract.py
- [x] T021 [P] [US1] Write integration test verifying order message routes to Order Agent stub and bi_query routes to BI Agent stub, chitchat returns directly in tests/002-orchestrator-domain-agents/integration/test_intent_routing.py

### Implementation

- [x] T022 [P] [US1] Create orchestrator/models.py with OrchestratorState (TypedDict for LangGraph), IntentResult, ChatRequest, ChatResponse Pydantic v2 models matching contracts/chat.json schema
- [x] T023 [P] [US1] Create orchestrator/prompts/intent_classify_v1.md with system prompt declaring 4 intent classes and 8 Vietnamese few-shot examples (2 order, 2 bi_query, 2 chitchat, 2 edge cases)
- [x] T024 [P] [US1] Create orchestrator/prompts/plan_v1.md with ExecutionPlan generation prompt: goal extraction, single-step plans for MVP, CoT reasoning instructions
- [x] T025 [US1] Implement orchestrator/nodes/intent_classify.py: call GPT-4o-mini with json_schema Structured Outputs (intent + confidence fields), escalate to GPT-4o when confidence < 0.72, log model_used and escalated fields
- [x] T026 [US1] Implement orchestrator/nodes/plan.py: call GPT-4o to generate ExecutionPlan, immediately persist plan to Redis as plan:{plan_id} with 1-hour TTL (Constitution V), set plan.status = active
- [x] T027 [US1] Implement orchestrator/nodes/a2a_dispatch.py: iterate plan steps in order, call orchestrator/a2a_client.py per step, store A2A task_id in PlanStep.task_id, handle A2A timeout by triggering replan node (max 3 attempts per MAX_REPLAN_ATTEMPTS)
- [x] T028 [US1] Implement orchestrator/nodes/aggregate.py: handle chitchat with direct GPT-4o-mini response (no A2A), synthesize Domain Agent A2AResult.output into user-facing Vietnamese reply
- [x] T029 [US1] Build orchestrator/graph.py: LangGraph StateGraph with nodes classify_intent→plan_execution→dispatch_to_agent→aggregate_response, replan conditional edge (replan_count < 3), chitchat short-circuit to aggregate
- [x] T030 [US1] Implement POST /chat endpoint in orchestrator/main.py: parse ChatRequest, set auth ContextVar (token + tenant_id), resolve or create session_id, invoke graph with thread_id=session_id, return ChatResponse

### Agent Discovery — AgentRegistry

- [x] T074 [US1] Create orchestrator/agent_registry.py with AgentRegistry class: from_env() reads AGENT_SEED_URLS (comma-separated base URLs), start() fetches cards + spawns background refresh task, _refresh() GETs {url}/.well-known/agent.json per seed URL with 5s timeout, marks unhealthy on failure, build_prompt_context() renders healthy agents as markdown for prompt injection, get_a2a_endpoint(agent_name) returns a2a_endpoint from card
- [x] T075 [US1] Update orchestrator/main.py lifespan: instantiate AgentRegistry.from_env(), call await registry.start(), store as app.state.registry; call await registry.stop() on shutdown; pass registry=request.app.state.registry to invoke_chat()
- [x] T076 [US1] Update orchestrator/nodes/plan.py generate_plan() to accept registry param; inject registry.build_prompt_context() into system prompt via {agent_manifest} placeholder in plan_v1.md; fallback to hardcoded order-agent/bi-agent list when registry=None
- [x] T077 [US1] Update orchestrator/nodes/a2a_dispatch.py dispatch_plan() to accept registry param; add _resolve_agent_url(agent_name, registry) that prefers registry.get_a2a_endpoint() and falls back to FALLBACK_AGENT_URLS env-var dict
- [x] T078 Add AGENT_SEED_URLS to .env.example (http://localhost:8002,http://localhost:8003) and docker-compose.yml orchestrator env (http://order-agent:8002,http://bi-agent:8003); update orchestrator/prompts/plan_v1.md to use {agent_manifest} placeholder with routing rules block

**Checkpoint**: Intent routing fully functional — POST /chat classifies and routes correctly with session tracking; AgentRegistry discovers agents dynamically (new agent appears in prompt ≤ 60s after startup)

---

## Phase 4b: User Story 7 — Plan Visibility (Priority: P2)

**Goal**: Every processed request generates a dual-layer plan (routing + display). The display layer is persisted to PostgreSQL and accessible via polling endpoints, enabling frontends to show real-time progress with Vietnamese business labels.

**Independent Test**: Submit a request, then poll `GET /plans/{session_id}/current` — verify plan appears within 200ms, sub-goal status transitions `pending` → `running` → `completed` as A2A tasks progress, and `agent_label` (not `agent_name`) is exposed in the response.

### Contract & Integration Tests (write first — must FAIL before implementation)

- [x] T079 [P] [US7] Write contract tests for GET /plans/{session_id}/current (200 with plan+sub_goals array, 404 when no plan) and GET /plans/{plan_id} (200 schema, 404) in tests/002-orchestrator-domain-agents/contract/test_plan_contract.py
- [x] T080 [P] [US7] Write integration test verifying sub-goal status transitions (pending→running→completed) reflect A2A task polling in real-time, and that completed parent plan has status=completed when all sub-goals done in tests/002-orchestrator-domain-agents/integration/test_plan_visibility.py

### Implementation

- [x] T081 [P] [US7] Add DisplayPlan and PlanSubGoal Pydantic v2 models to orchestrator/models.py: DisplayPlan(id, session_id, tenant_id, goal, status, created_at, updated_at) and PlanSubGoal(id, plan_id, sequence, title, agent_name, agent_label, a2a_task_id, status, result_summary, started_at, completed_at)
- [x] T082 [US7] Create orchestrator/migrations/001_plans.sql: CREATE TABLE plans(id UUID PK, session_id, tenant_id, user_message, goal, status DEFAULT 'pending', created_at, updated_at) + CREATE TABLE plan_sub_goals(id UUID PK, plan_id FK CASCADE, sequence, title, agent_name, agent_label, a2a_task_id NULLABLE, status DEFAULT 'pending', result_summary, started_at, completed_at) + indexes idx_plans_session(session_id, tenant_id), idx_sub_goals_plan(plan_id, sequence), idx_sub_goals_task(a2a_task_id)
- [x] T083 [US7] Create orchestrator/core/__init__.py and orchestrator/core/plan_service.py with PlanService(db: asyncpg.Pool): create_plan(session_id, tenant_id, user_message, display) → str plan_id (inserts plans row status=running + all plan_sub_goals rows), link_task(plan_id, sequence, a2a_task_id) (UPDATE sub_goal: a2a_task_id, status=running, started_at=now()), sync_from_task(a2a_task_id, task_status, result_summary) (UPDATE sub_goal via idx_sub_goals_task; call _maybe_complete_plan), _maybe_complete_plan(a2a_task_id) (set plans.status=completed/failed if all sub_goals terminal), get_plan(plan_id) → dict with plan + sub_goals list
- [x] T084 [US7] Update orchestrator/prompts/plan_v1.md to require dual-layer JSON output with top-level keys "display" (goal: Vietnamese business description, sub_goals: [{sequence, title, agent_name, agent_label}]) and "routing" (steps: [{agent, skill}]); add agent_label mapping table (order-agent→"Tạo & Quản Lý Đơn Hàng", bi-agent→"Báo Cáo & Phân Tích"); update both few-shot examples to match new output schema; keep {agent_manifest} placeholder
- [x] T085 [US7] Update orchestrator/nodes/plan.py to parse dual-layer GPT-4o output (display + routing keys); validate with Pydantic; store routing steps in OrchestratorState for A2A dispatch; call plan_service.create_plan() with the display layer immediately after parsing; store plan_id in state; params still always injected server-side (not from GPT output)
- [x] T086 [US7] Update orchestrator/nodes/a2a_dispatch.py to accept plan_service and plan_id from state; call plan_service.link_task(plan_id, step_sequence, task_id) immediately after each POST /a2a/tasks returns 202; call plan_service.sync_from_task(task_id, task.status, result_summary) on each polling iteration
- [x] T087 [US7] Update orchestrator/main.py lifespan to create asyncpg.Pool from POSTGRES_DSN env var, run migration 001_plans.sql on startup (CREATE TABLE IF NOT EXISTS), store as app.state.db_pool; add GET /plans/{session_id}/current and GET /plans/{plan_id} route handlers using PlanService(app.state.db_pool); pass plan_service instance into invoke_chat() call
- [x] T088 [P] [US7] Update specs/002-orchestrator-domain-agents/data-model.md to add DisplayPlan and PlanSubGoal entity tables with all fields, storage (PostgreSQL), state transitions (pending→running→completed/failed), and update entity overview diagram to show DisplayPlan linked from UserSession

**Checkpoint**: Plan Visibility fully functional — every /chat request creates a PostgreSQL plan record; GET /plans/{session_id}/current returns live sub-goal statuses; completed sub-goals show result_summary

---

## Phase 5: User Story 2 — Order Creation via Natural Language (Priority: P1)

**Goal**: Order Agent extracts Vietnamese order entities, matches products via FAISS, confirms with user, and submits to the backend via Tool Registry.

**Independent Test**: Submit 20 NL order messages to Order Agent A2A endpoint. Verify ≥ 17/20 produce correct previews with right customer, products, quantities, and table. Verify confirmation interrupt triggers input-required status.

### Contract & Integration Tests (write first — must FAIL before implementation)

- [x] T031 [P] [US2] Write contract tests for Order Agent A2A result output schema (status/order_draft/order_result/unresolved_items fields) against contracts/order-agent-response.json in tests/002-orchestrator-domain-agents/contract/test_order_agent_contract.py
- [x] T032 [P] [US2] Write integration test for full multi-turn order flow: submit order → input-required → confirm → completed with order_code in tests/002-orchestrator-domain-agents/integration/test_order_flow.py
- [x] T033 [P] [US2] Write unit tests for Vietnamese NLP utilities in tests/002-orchestrator-domain-agents/unit/test_vn_utils.py: 50 cases covering honorific stripping (anh/chị/em/bác/cô/chú/ông/bà), number word conversion (một–mười hai), unicode NFC normalization
- [x] T034 [P] [US2] Write unit tests for ProductMatcher in tests/002-orchestrator-domain-agents/unit/test_product_matcher.py: ≥ 0.85 → auto, 0.65–0.84 → rerank candidates, < 0.65 → ask_user, atomic index refresh

### Implementation

- [x] T035 [P] [US2] Create order_agent/models.py with Pydantic v2 models: OrderAgentState, OrderEntities, OrderItem, ProductMatch (match_status: auto/rerank/ask_user/not_found), OrderDraft, ResolvedOrderItem
- [x] T036 [P] [US2] Implement order_agent/vn_utils.py pure functions: normalize_text() (NFC+lowercase+strip), strip_honorifics(), words_to_numbers() (VN_NUMBERS dict from research.md Decision 7), extract_product_note()
- [x] T037 [P] [US2] Create order_agent/prompts/entity_extract_v1.md: system prompt with entity schema and 12 Vietnamese few-shot examples covering single item, multi-item, table number, honorifics, quantity words, and notes
- [x] T038 [US2] Implement order_agent/product_matcher.py: ProductMatcher class with FAISS IndexFlatIP (L2-normalized vectors = cosine), build_index(products) loading paraphrase-multilingual-MiniLM-L12-v2, search(query, top_k=3) returning ProductMatch list, atomic refresh via asyncio.create_task every 30 minutes at startup
- [x] T039 [US2] Implement order_agent/nodes/extract_entities.py: normalize input via vn_utils, call GPT-4o-mini with json_schema Structured Outputs to produce OrderEntities, handle intent_modifier (new/add/remove/cancel)
- [x] T040 [US2] Implement order_agent/nodes/match_products.py: call ProductMatcher.search() per OrderItem, route by cosine threshold (≥ 0.85 auto, 0.65–0.84 GPT-4o-mini rerank from top-3, < 0.65 ask_user), populate ProductMatch list in state
- [x] T041 [US2] Implement order_agent/nodes/check_customer.py: call Tool Registry customer__get_customers with customer_name, handle not-found (set input-required: ask to create), handle multiple matches (set input-required: show top candidates)
- [x] T042 [US2] Implement order_agent/nodes/preview.py: build OrderDraft from matched items + customer, calculate total_estimate, format Vietnamese order preview message listing all items, prices, table number, and total
- [x] T043 [US2] Implement order_agent/nodes/confirm.py: LangGraph node that halts execution and sets A2ATask status to input-required with preview as input_request; resumes when continuation.user_input = "xác nhận" or cancels on "huỷ"
- [x] T044 [US2] Implement order_agent/nodes/submit_order.py: call Tool Registry order__create_order with ResolvedOrderItem list, populate A2AResult with output={status: confirmed, order_result: {order_code, ...}}, reasoning_summary
- [x] T045 [US2] Build order_agent/graph.py: LangGraph StateGraph extract_entities→match_products→check_customer→build_preview→confirm→submit_order, interrupt_before=["submit_order"] for confirmation gate
- [x] T046 [US2] Wire order_agent/graph.py into order_agent/a2a_server.py background task processor: on new task invoke graph, on continuation with task_id resume via ainvoke(None, {configurable: {thread_id: task_id}})

**Checkpoint**: End-to-end order creation works — NL input → FAISS product match → preview → confirm → the backend order

---

## Phase 6: User Story 3 — Business Intelligence Queries (Priority: P1)

**Goal**: BI Agent translates Vietnamese questions to SQL, executes read-only queries against analytics DB via Tool Registry, and returns formatted Vietnamese answers.

**Independent Test**: Submit 15 BI queries to BI Agent A2A endpoint. Verify ≥ 12/15 return correct formatted answers. Verify non-SELECT queries are rejected with status=rejected.

### Contract & Integration Tests (write first — must FAIL before implementation)

- [x] T047 [P] [US3] Write contract tests for BI Agent A2A result output schema (status/message/generated_sql/row_count/data fields) against contracts/bi-agent-response.json in tests/002-orchestrator-domain-agents/contract/test_bi_agent_contract.py
- [x] T048 [P] [US3] Write integration test for BI query flow: Vietnamese question → SQL → execute → formatted answer, plus non-SELECT rejection in tests/002-orchestrator-domain-agents/integration/test_bi_query.py
- [x] T049 [P] [US3] Write unit tests for NL2SQL safety: SELECT passes, DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER rejected, automatic LIMIT injection when not present in tests/002-orchestrator-domain-agents/unit/test_nl2sql.py

### Implementation

- [x] T050 [P] [US3] Create bi_agent/models.py with BIAgentState (TypedDict for LangGraph) and BIQueryResult Pydantic v2 model
- [x] T051 [P] [US3] Create config/bi_schema.yaml: table allowlist (orders, customers, products, inventory, order_items), column descriptions per table, business glossary (doanh thu=revenue, công nợ=debt, khách hàng=customer, đơn hàng=order)
- [x] T052 [P] [US3] Create bi_agent/prompts/nl2sql_v1.md: NL2SQL system prompt with schema injection placeholder, 6 Vietnamese→SQL few-shot examples covering revenue SUM, customer ranking, debt threshold, date grouping
- [x] T053 [P] [US3] Create bi_agent/prompts/format_response_v1.md: Vietnamese response formatting prompt with examples for summary text, ranked lists, and key figures
- [x] T054 [US3] Implement bi_agent/nodes/schema_explorer.py: load config/bi_schema.yaml allowlist at startup, produce schema context string (table names + column descriptions + glossary) for NL2SQL prompt injection
- [x] T055 [US3] Implement bi_agent/nodes/nl2sql.py: inject schema context into nl2sql_v1.md prompt, call GPT-4o with json_schema Structured Outputs (sql + explanation fields), temperature=0
- [x] T056 [US3] Implement bi_agent/nodes/safety_check.py: parse generated SQL, reject if not SELECT (blocklist: DROP, DELETE, UPDATE, INSERT, TRUNCATE, ALTER, EXEC, CALL), set output.status=rejected with error_reason
- [x] T057 [US3] Implement bi_agent/nodes/execute_query.py: call Tool Registry bi__run_query with generated SQL, inject LIMIT 500 if no LIMIT present, set limit_applied=True in result
- [x] T058 [US3] Implement bi_agent/nodes/format_response.py: call GPT-4o-mini with format_response_v1.md prompt + raw SQL rows, produce Vietnamese human-readable summary with currency formatting
- [x] T059 [US3] Build bi_agent/graph.py: LangGraph StateGraph schema_explorer→generate_sql→safety_check→execute_query→format_response, short-circuit edge from safety_check to terminal when rejected
- [x] T060 [US3] Wire bi_agent/graph.py into bi_agent/a2a_server.py background task processor: invoke graph per task, write A2AResult with output matching bi-agent-response.json contract

**Checkpoint**: End-to-end BI queries work — Vietnamese question → SQL → execute → formatted Vietnamese answer, non-SELECT rejected

---

## Phase 7: User Story 4 — Multi-Turn Conversation State (Priority: P2)

**Goal**: Sessions persist across turns via Redis checkpointer. Order Agent resumes interrupted confirmation flows. Orchestrator injects last-3-turns history into LLM context.

**Independent Test**: Run 3-turn order conversation (describe → add item → confirm). Verify final order contains all items from all turns without duplication.

- [x] T061 [US4] Implement orchestrator/session.py: UserSession Pydantic model, Redis store at session:{session_id} as JSON with 30-min TTL, get_or_create_session(), append_turn(), get_last_n_turns(n=3), expire on 30 min inactivity
- [x] T062 [US4] Integrate AsyncRedisSaver checkpointer into orchestrator/graph.py: initialize from REDIS_URL at lifespan, pass thread_id=session_id in graph.ainvoke() config, set checkpoint TTL via pin_version or key prefix
- [x] T063 [US4] Integrate AsyncRedisSaver checkpointer into order_agent/graph.py: initialize from REDIS_URL at lifespan, thread_id=task_id for Order Agent graphs, resume interrupted confirm node via ainvoke(None, {configurable: {thread_id: task_id}})
- [x] T064 [US4] Implement input-required resume flow in order_agent/a2a_server.py: when POST /a2a/tasks receives params.continuation.task_id, load existing LangGraph checkpoint, call ainvoke(None, config) with continuation.user_input injected into state as the confirmation signal

**Checkpoint**: Multi-turn order confirmation works across separate HTTP requests within the same session

---

## Phase 7b: User Story 8 — Domain Agent Memory (Priority: P2)

**Goal**: Both Domain Agents accumulate semantic knowledge (product aliases, customer preferences, SQL patterns) and episodic task history over time. Before each task the agent retrieves relevant memories and injects them into the system prompt. After each task it extracts ≤ 3 facts via GPT-4o-mini and records task history. All memory operations degrade gracefully when PostgreSQL is unavailable.

**Independent Test**: Submit an Order Agent task that resolves alias "ba đen" → "cafe đen đá size L". Verify a `product_alias` row is upserted into `agent_memory`. On the second identical task the alias is retrieved from memory and injected into the system prompt — no user confirmation needed.

### Tests (write first — must FAIL before implementation)

- [x] T089 [P] [US8] Write integration tests for MemoryService round-trip: store product_alias → retrieve returns it ranked first, find_similar_tasks returns past success, record_task inserts history row, graceful degradation returns empty list when db=None in tests/002-orchestrator-domain-agents/integration/test_agent_memory.py
- [x] T090 [P] [US8] Write unit tests for MemoryService in tests/002-orchestrator-domain-agents/unit/test_memory_service.py: retrieve ordering (confidence DESC, usage_count DESC), UPSERT conflict takes GREATEST(confidence), input_summary must not contain raw PII patterns (regex assert), best-effort _store_learnings swallows JSON parse errors

### Implementation

- [x] T091 [US8] Create orchestrator/migrations/002_agent_memory.sql: CREATE TABLE IF NOT EXISTS agent_memory(id UUID PK DEFAULT gen_random_uuid(), agent_name VARCHAR(100), tenant_id VARCHAR(128), memory_type VARCHAR(50), key TEXT, content TEXT, confidence FLOAT DEFAULT 0.8, usage_count INT DEFAULT 0, last_used_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(), UNIQUE(agent_name,tenant_id,memory_type,key)) + CREATE TABLE IF NOT EXISTS agent_task_history(id UUID PK DEFAULT gen_random_uuid(), agent_name VARCHAR(100), tenant_id VARCHAR(128), plan_id UUID REFERENCES plans(id), skill VARCHAR(100), input_summary TEXT, outcome VARCHAR(20), key_decisions JSONB, learnings TEXT, duration_ms INT, created_at TIMESTAMPTZ DEFAULT now()) + indexes: idx_mem_agent_tenant, idx_mem_type_key, idx_history_agent, idx_history_skill, idx_history_plan
- [x] T092 [US8] Create shared/memory_service.py with MemoryService(db: asyncpg.Pool, agent_name: str): retrieve(tenant_id, query_context, limit=5) → SELECT WHERE agent_name+tenant_id, ILIKE on key+content, ORDER BY confidence DESC/usage_count DESC/last_used_at DESC, bump _usage after fetch; store(tenant_id, memory_type, key, content, confidence=0.8) → INSERT ... ON CONFLICT DO UPDATE SET content, confidence=GREATEST(...), updated_at=now(); find_similar_tasks(tenant_id, skill, input_summary, limit=3) → SELECT WHERE outcome='success' AND input_summary ILIKE ORDER BY created_at DESC; record_task(tenant_id, plan_id, skill, input_summary, outcome, key_decisions, learnings, duration_ms) → INSERT agent_task_history
- [x] T093 [P] [US8] Create order_agent/core/__init__.py and order_agent/core/react_loop.py: MemoryAwareReActLoop class with run(task, memory): (1) if memory: retrieve(keyword=task.input_summary[:50]) + find_similar_tasks; (2) _build_system_prompt(base_prompt, semantic_mem, similar_tasks) injecting "## Kiến Thức Tích Lũy" and "## Bài Học Từ Task Tương Tự" blocks; (3) ReAct loop max 10 iterations via llm_call + tool execution; (4) _extract_learnings via GPT-4o-mini with EXTRACT_LEARNINGS_PROMPT (types: product_alias, customer_pref, vn_expression, order_pattern); (5) _store_learnings best-effort; (6) memory.record_task() with sanitised input_summary (no raw customer names)
- [x] T094 [P] [US8] Create bi_agent/core/__init__.py and bi_agent/core/react_loop.py: MemoryAwareReActLoop (BI variant) — same run() structure as T093 but tools are NL2SQL pipeline; learning extraction types: sql_pattern, glossary_fix, column_alias, query_template; inject retrieved memories before nl2sql call
- [x] T095 [US8] Update order_agent/main.py lifespan: if POSTGRES_DSN set, create asyncpg.Pool, run migration 002_agent_memory.sql (idempotent), instantiate MemoryService(db, "order-agent"), store as app.state.memory; else app.state.memory = None; close pool on shutdown
- [x] T096 [US8] Update bi_agent/main.py lifespan: same pattern as T095 with agent_name="bi-agent"; run same 002_agent_memory.sql migration (idempotent — CREATE TABLE IF NOT EXISTS)
- [x] T097 [US8] Update order_agent/a2a_server.py task background processor: pass app.state.memory to MemoryAwareReActLoop(tools).run(task, memory); when memory=None pass None and MemoryAwareReActLoop must skip all DB calls; record outcome in task history after task completes or fails
- [x] T098 [US8] Update bi_agent/a2a_server.py task background processor: same pattern as T097 using BI MemoryAwareReActLoop

**Checkpoint**: Domain Agent Memory functional — alias upserted after first task; retrieved and injected on second task; agent_task_history row created; zero PII in input_summary; all operations skipped when POSTGRES_DSN not set

---

## Phase 8b: User Story 9 — Orchestrator Memory (Priority: P2)

**Goal**: The Orchestrator learns routing strategies, plan templates, and user patterns at the meta-level. Before every plan the Plan node retrieves relevant routing patterns and similar completed plans, injecting them into the GPT-4o system prompt via `{routing_memory}` and `{similar_plans}` placeholders. After each plan completes, a fire-and-forget GPT-4o-mini call extracts ≤ 3 routing insights and upserts them to `orchestrator_memory`. All operations degrade gracefully when PostgreSQL is unavailable.

**Independent Test**: Submit a combined order+BI request, complete it. Trigger `extract_and_store()`. On the next similar request verify the `routing_pattern` entry appears in the rendered system prompt sent to GPT-4o.

### Tests (write first — must FAIL before implementation)

- [x] T099 [P] [US9] Write integration tests for OrchestratorMemoryService round-trip: store_pattern upserts a routing_pattern row, retrieve_patterns returns it ranked first, find_similar_plans queries completed plans table, extract_and_store calls store_pattern for each extracted fact, graceful degradation returns empty list when db=None in tests/002-orchestrator-domain-agents/integration/test_orchestrator_memory.py
- [x] T100 [P] [US9] Write unit tests for OrchestratorMemoryService in tests/002-orchestrator-domain-agents/unit/test_orchestrator_memory_service.py: retrieve_patterns SQL orders by confidence DESC/usage_count DESC, UPSERT ON CONFLICT uses GREATEST(confidence), extract_and_store swallows LLM errors (best-effort), store_pattern with None db is a no-op

### Implementation

- [x] T101 [US9] Create orchestrator/migrations/003_orchestrator_memory.sql: CREATE TABLE IF NOT EXISTS orchestrator_memory(id UUID PK DEFAULT gen_random_uuid(), tenant_id VARCHAR(128) NOT NULL, memory_type VARCHAR(50) NOT NULL, key TEXT NOT NULL, content TEXT NOT NULL, confidence FLOAT NOT NULL DEFAULT 0.8, usage_count INT NOT NULL DEFAULT 0, last_used_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()); CREATE UNIQUE INDEX IF NOT EXISTS idx_orch_mem_key ON orchestrator_memory(tenant_id, memory_type, key); CREATE INDEX IF NOT EXISTS idx_orch_mem_type ON orchestrator_memory(tenant_id, memory_type)
- [x] T102 [US9] Create orchestrator/core/orchestrator_memory.py with OrchestratorMemoryService(db: asyncpg.Pool): retrieve_patterns(tenant_id, intent_class, request_summary, limit=4) → SELECT WHERE key ILIKE %intent_class% OR content ILIKE %request_summary[:40]%, ORDER BY confidence DESC, usage_count DESC; store_pattern(tenant_id, memory_type, key, content, confidence=0.8) → INSERT ... ON CONFLICT(tenant_id,memory_type,key) DO UPDATE SET content, confidence=GREATEST(orchestrator_memory.confidence, $5), updated_at=now(); find_similar_plans(tenant_id, user_message, limit=3) → SELECT p.goal, p.user_message, json_agg(sg) FROM plans JOIN plan_sub_goals sg WHERE p.status='completed' AND p.user_message ILIKE %user_message[:40]% ORDER BY p.created_at DESC; extract_and_store(tenant_id, plan, sub_goals) → best-effort GPT-4o-mini call via ROUTING_LEARNINGS_PROMPT then store_pattern per fact; all methods return [] / no-op when self.db is None
- [x] T103 [P] [US9] Add "extract_routing_learnings": "fast" entry to _MODEL_ROUTING dict in shared/llm_client.py (GPT-4o-mini for post-plan routing insight extraction)
- [x] T104 [US9] Update orchestrator/nodes/plan.py generate_plan() to: (1) accept memory: OrchestratorMemoryService | None param; (2) if memory: await retrieve_patterns(tenant_id, intent, user_message) + find_similar_plans(tenant_id, user_message); (3) render {routing_memory} block ("## Routing Patterns\n" + content lines) and {similar_plans} block ("## Plan Tương Tự\n" + goal+sub_goals); (4) format ORCHESTRATOR_SYSTEM_TEMPLATE with agent_manifest + routing_memory + similar_plans; (5) after plan stored via PlanService, schedule asyncio.create_task(_fire_and_forget_extract(memory, tenant_id, plan, sub_goals)); graceful: when memory=None render empty strings for both placeholders
- [x] T105 [US9] Update orchestrator/prompts/plan_v1.md system template to include {routing_memory} and {similar_plans} placeholders after {agent_manifest}; add routing rules block: "- Tham khảo Routing Patterns để chọn agent combination tốt nhất; - Tham khảo Plan Tương Tự để tái dùng cấu trúc đã thành công; - LUÔN ưu tiên context hiện tại hơn memory nếu có mâu thuẫn"; update both few-shot examples to show non-empty routing_memory injection
- [x] T106 [US9] Update orchestrator/main.py lifespan: after asyncpg.Pool is created, run migration 003_orchestrator_memory.sql (idempotent — IF NOT EXISTS), instantiate OrchestratorMemoryService(app.state.db_pool), store as app.state.orchestrator_memory; when POSTGRES_DSN not set, app.state.orchestrator_memory = None; pass to invoke_chat() via graph
- [x] T107 [US9] Update orchestrator/graph.py invoke_chat() to thread orchestrator_memory from app.state through OrchestratorState and into plan node; plan node receives memory param; graceful degradation verified end-to-end when orchestrator_memory=None

**Checkpoint**: Orchestrator Memory functional — routing_pattern upserted after first combined request; retrieved and injected into plan prompt on second request; extract_and_store never blocks response; all operations skipped when POSTGRES_DSN not set

---

## Phase 8c: User Story 10 — HITL Confirmation for Mutating Tools (Priority: P1)

**Goal**: Every mutating tool call (`requires_confirmation: true` in tools.yaml) pauses the ReAct loop, generates a Vietnamese confirmation message describing the action and data impact, and waits for user approval. Four response branches: `confirm` (execute tool), `modify` (inject feedback, LLM re-reasons), `cancel` (inject cancellation, LLM re-reasons), `scope_change` (signal Orchestrator). Read-only tools bypass HITL entirely.

**Independent Test**: Submit an order creation task. Verify A2A transitions to `input_required` before `order__create_order` executes. Send "xác nhận" continuation → order created (confirm branch). Send "huỷ" continuation → order not created (cancel branch). Verify `bi__run_query` (read-only) never triggers HITL pause.

### Tests (write first — must FAIL before implementation)

- [x] T108 [P] [US10] Write integration tests for HITL flow: mutating tool pauses ReAct loop and sets A2A to input_required, confirm branch executes tool and completes task, modify branch injects feedback and loop re-reasons, cancel branch acknowledges without executing, scope_change returns __scope_change__ signal; read-only tool (bi__run_query) bypasses HITL entirely in tests/002-orchestrator-domain-agents/integration/test_hitl_flow.py
- [x] T109 [P] [US10] Write unit tests for HITL in tests/002-orchestrator-domain-agents/unit/test_hitl_unit.py: _classify_hitl_response returns confirm/modify/cancel/scope_change for corresponding Vietnamese inputs, _generate_confirm_message output contains tool action and impact description in Vietnamese, _execute_tool with requires_confirmation=False calls tool directly without pausing, _execute_tool with requires_confirmation=True returns __hitl__ dict without executing tool

### Implementation

- [x] T110 [P] [US10] Add `requires_confirmation: true` and `impact_template: "..."` fields to mutating tools in config/tools.yaml: order__create_order (impact: "Tạo đơn hàng mới — không thể huỷ sau khi xác nhận"), order__update_order, order__cancel_order, customer__create_customer; leave requires_confirmation absent (default false) for read-only tools get_customers and bi__run_query
- [x] T111 [P] [US10] Add `"confirm_message": "fast"` and `"classify_hitl_response": "fast"` entries to _MODEL_ROUTING dict in shared/llm_client.py so HITL LLM calls use GPT-4o-mini (low latency gate)
- [x] T112 [US10] Add CONFIRM_PROMPT and CLASSIFY_HITL_PROMPT string constants to order_agent/core/react_loop.py; implement _classify_hitl_response(self, user_response: str, tool_name: str, pending_args: dict) → Literal["confirm","modify","cancel","scope_change"] using GPT-4o-mini json_schema Structured Output; implement _generate_confirm_message(self, tool_name: str, args: dict, impact_template: str) → str using GPT-4o-mini with CONFIRM_PROMPT + impact_template substitution
- [x] T113 [US10] Update _execute_tool(self, tool_name, args, messages, plan_id) in order_agent/core/react_loop.py: fetch tool_def from ToolRegistryClient; if tool_def.get("requires_confirmation") is True, call _generate_confirm_message() and return {"__hitl__": True, "question": confirm_msg, "pending_tool": tool_name, "pending_args": args} without executing the tool; if False, execute tool immediately as before
- [x] T114 [US10] Implement resume_after_hitl(self, user_response: str, pending_tool: str, pending_args: dict, messages: list) in order_agent/core/react_loop.py: call _classify_hitl_response(); confirm → execute pending_tool with pending_args + continue ReAct loop from next iteration; modify → append user_response to messages and continue loop (LLM re-reasons with updated context); cancel → append "Đã huỷ thao tác" to messages and continue loop; scope_change → return {"__scope_change__": True, "new_request": user_response}
- [x] T115 [US10] Update order_agent/a2a_server.py task continuation routing: when POST /a2a/tasks has params.continuation.task_id AND fetched task has state["hitl_pending"] == True, call MemoryAwareReActLoop.resume_after_hitl() instead of graph checkpoint resume; set task.state["hitl_pending"] = True when _execute_tool returns __hitl__ dict; distinguish from order-preview continuation (which uses LangGraph ainvoke(None, config))
- [x] T116 [P] [US10] Apply same HITL pattern to bi_agent/core/react_loop.py: add CONFIRM_PROMPT + CLASSIFY_HITL_PROMPT constants, implement _classify_hitl_response(), _generate_confirm_message(), update _execute_tool() to check requires_confirmation, implement resume_after_hitl() — identical logic to order_agent variant for future BI write tools
- [x] T117 [P] [US10] Update bi_agent/a2a_server.py: same HITL continuation routing as T115 — detect task.state["hitl_pending"] flag and route to MemoryAwareReActLoop.resume_after_hitl() when present

**Checkpoint**: HITL fully functional — every mutating tool call pauses before execution; confirm/modify/cancel/scope_change branches all route correctly; read-only tools execute without interruption; ≤ 500ms added latency from HITL classification (SC-021)

---

## Phase 8d: User Story 11 — Parallel & Sequential Task Dispatch (Priority: P1)

**Goal**: The Orchestrator dispatches independent plan steps in parallel via `asyncio.gather` and sequential steps in dependency order. Each Domain Agent receives an `A2ATaskPayload` with full context: `instructions` (per-step goal), `original_message`, `conversation_history` (last 6 turns), and `dependency_results` from upstream agents. `build_agent_system_prompt(payload, memory_context)` assembles these into the LLM system prompt.

**Independent Test**: Submit a combined order+BI request producing two independent steps (`depends_on: []`). Verify both A2A tasks are submitted within 200ms of each other. Submit a sequential request where step 2 `depends_on: ["order-agent"]` — verify step 2's `A2ATaskPayload.dependency_results` contains step 1's result.

### Tests (write first — must FAIL before implementation)

- [x] T118 [P] [US11] Write integration tests for DispatchEngine: parallel dispatch submits two independent steps concurrently (verify timestamps within 200ms), sequential dispatch waits for dependency and injects result into A2ATaskPayload.dependency_results, upstream failure marks downstream step as failed in tests/002-orchestrator-domain-agents/integration/test_dispatch_engine.py
- [x] T119 [P] [US11] Write unit tests for DispatchEngine in tests/002-orchestrator-domain-agents/unit/test_dispatch_engine.py: steps with empty depends_on dispatched via asyncio.gather in the same round, downstream step dispatched only after upstream completes, A2ATaskPayload.dependency_results populated from upstream A2AResult.output, upstream failure sets downstream status=failed without dispatching

### Implementation

- [x] T120 [P] [US11] Update shared/a2a/models.py: add A2ATaskPayload Pydantic v2 model with fields task_id (UUID str), plan_id (UUID str), sub_goal_sequence (int), session_id (str), tenant_id (str), original_message (str), skill (str), instructions (str, default ""), conversation_history (list[dict], default []), dependency_results (dict[str, dict], default {}); update PlanStep to add depends_on: list[str] = [] (agent names, not step_ids) and instructions: str = "" fields; both fields required in schema, default empty for backward compatibility
- [x] T121 [US11] Create orchestrator/core/dispatch_engine.py: DispatchEngine class with execute(steps: list[PlanStep], session: UserSession, plan_id: str, tenant_id: str, plan_service: PlanService, registry: AgentRegistry) → dict[str, dict]; execute() iterates rounds: find ready steps (all agents in depends_on present in results dict), dispatch ready steps in parallel via asyncio.gather(_dispatch_one(...)), collect results, repeat until all steps dispatched or failed; if upstream step result["status"] == "failed", mark all downstream dependents failed without dispatching; _dispatch_one(step, dependency_results, session, plan_id, tenant_id) → builds A2ATaskPayload(task_id=new_uuid, plan_id, sub_goal_sequence=step.sequence, session_id, tenant_id, original_message=session.last_user_message, skill=step.skill, instructions=step.instructions, conversation_history=session.get_last_n_turns(6), dependency_results), calls plan_service.link_task(), submits to agent URL via a2a_client, polls for result, calls plan_service.sync_from_task(), returns result dict
- [x] T122 [US11] Update orchestrator/prompts/plan_v1.md: add to routing step schema `"depends_on": ["agent-name-if-dependency"]` and `"instructions": "Vietnamese description of this specific step's goal"`; instruct LLM: steps with no dependencies MUST have `depends_on: []`; add dependency example (step 2 depends_on order-agent); update both few-shot examples to include depends_on + instructions for every routing step
- [x] T123 [US11] Update orchestrator/nodes/a2a_dispatch.py dispatch_plan(): replace manual sequential A2A loop with DispatchEngine instantiation; pass plan_service, registry, session to DispatchEngine.execute(steps); results dict returned by DispatchEngine replaces manual step result collection; keep existing plan_service.link_task/sync_from_task calls inside DispatchEngine._dispatch_one
- [x] T124 [P] [US11] Update order_agent/core/react_loop.py: add build_agent_system_prompt(payload: A2ATaskPayload | None, memory_context: str) → str; sections: base agent role prompt, then if payload: `## Nhiệm Vụ Hiện Tại\n{payload.instructions}`, `## Yêu Cầu Gốc\n{payload.original_message}`, then if payload.dependency_results: `## Kết Quả Từ Bước Trước\n{formatted_dependency_results}`, then memory_context; update MemoryAwareReActLoop.run() signature to accept payload: A2ATaskPayload | None = None and call build_agent_system_prompt(payload, memory_context) instead of bare base prompt
- [x] T125 [P] [US11] Update bi_agent/core/react_loop.py: same build_agent_system_prompt() pattern as T124 — base BI agent role prompt + instructions + original_message + dependency_results (if non-empty) + memory_context; update MemoryAwareReActLoop.run() signature identically
- [x] T126 [US11] Update order_agent/a2a_server.py _process_order_task(): at task start, attempt A2ATaskPayload.model_validate(task.params) wrapped in try/except ValidationError; if valid payload, pass payload to MemoryAwareReActLoop.run(task_id=task_id, payload=payload, memory=memory); if validation fails (old dict-format params), pass payload=None for backward compatibility; extract session_id and tenant_id from payload if available
- [x] T127 [P] [US11] Update bi_agent/a2a_server.py _process_bi_task(): same A2ATaskPayload.model_validate(task.params) unpacking pattern as T126 with ValidationError fallback; pass payload to MemoryAwareReActLoop.run()

**Checkpoint**: Parallel & Sequential Task Dispatch functional — independent steps dispatched concurrently; downstream agents receive dependency_results; build_agent_system_prompt injects instructions + context into every Domain Agent LLM call (SC-022 + SC-023)

---

## Phase 8e: User Story 12 — Customer Agent (Priority: P1)

**Goal**: A dedicated `customer_agent` service (port 8004) handles all customer lifecycle operations via three A2A skills: `lookup_customer`, `create_customer`, `update_customer`. The Customer Agent uses `MemoryAwareReActLoop` with `contact_alias`/`customer_profile`/`lookup_pattern` memory types — on the second lookup of "anh Lâm" it resolves the `customer_id` directly from memory without calling the Tool Registry. `create_customer` and `update_customer` are guarded by the HITL gate (`requires_confirmation: true`). The Orchestrator discovers the Customer Agent via AgentRegistry (`AGENT_SEED_URLS`).

**Independent Test**: Submit a Customer Agent A2A task with skill `lookup_customer` — verify the task transitions `submitted → working → completed` and returns a customer list. Submit the same name lookup twice — verify the second call sets `memory_hit=True` in the result (resolved from `contact_alias` memory). Submit `create_customer` task — verify the A2A task transitions to `input_required` (HITL gate) before any record is created.

### Tests (write first — must FAIL before implementation)

- [X] T128 [P] [US12] Write contract tests for Customer Agent in tests/002-orchestrator-domain-agents/contract/test_customer_agent_contract.py: POST /a2a/tasks 202 returns task_id+status, GET /a2a/tasks/{id} returns status enum, GET /.well-known/agent.json returns agent card with name="Customer Agent" and skills=[lookup_customer, create_customer, update_customer], token not echoed in any response field
- [X] T129 [P] [US12] Write integration tests for Customer Agent in tests/002-orchestrator-domain-agents/integration/test_customer_agent.py: lookup_customer by name returns matching records, create_customer transitions to input_required (HITL pause before Tool Registry call), alias memory recall (2nd lookup for same alias resolves from contact_alias memory: result.memory_hit=True, customer__get_customers not called second time)

### Implementation

- [X] T130 [P] [US12] Create customer_agent/models.py: CustomerRecord (customer_id: str, name: str, phone: str | None, email: str | None), CustomerLookupResult (skill: str, customers: list[CustomerRecord], customer_id: str | None, action: str, memory_hit: bool), CustomerAgentState (TypedDict with messages: list, skill: str, payload: dict | None, result: dict | None)
- [X] T131 [P] [US12] Create customer_agent/prompts/customer_agent_v1.md: Customer Agent system prompt (role: quản lý khách hàng, tool namespace: customer) + 6 few-shot ReAct examples — lookup by name, lookup by phone, create_customer (HITL pause), update_customer (HITL pause), alias recall from memory, view purchase history
- [X] T132 [P] [US12] Update config/tools.yaml: add customer__update_customer tool (HTTP PUT /customers/{id}, namespace: customer, requires_confirmation: true, impact_template: "Cập nhật SĐT khách {name}: {old_phone} → {new_phone}."); add requires_confirmation: true + impact_template: "Tạo khách hàng mới: {name} ({phone}). Dữ liệu sẽ được lưu vào hệ thống." to existing customer__create_customer entry
- [X] T133 [US12] Create customer_agent/core/react_loop.py: MemoryAwareReActLoop (Customer Agent variant) with build_agent_system_prompt(payload, memory_context) following same pattern as order_agent/bi_agent; retrieve contact_alias/customer_profile/lookup_pattern memories before loop via MemoryService; HITL gate on create_customer and update_customer (requires_confirmation: true); on successful lookup store contact_alias entry mapping alias text → {customer_id, full_name, phone} with confidence=0.9; _extract_learnings (best-effort, GPT-4o-mini) extracts contact_alias/customer_profile/lookup_pattern facts; graceful degradation when POSTGRES_DSN not set (memory=None)
- [X] T134 [US12] Create customer_agent/a2a_server.py: POST /a2a/tasks (202) for skills lookup_customer/create_customer/update_customer; background _process_customer_task() using MemoryAwareReActLoop; A2ATaskPayload.model_validate(task.params) with ValidationError fallback to payload=None; HITL continuation via resume_after_hitl() matching order_agent/a2a_server.py pattern; GET /a2a/tasks/{id} status + result polling
- [X] T135 [US12] Create customer_agent/main.py: FastAPI app with A2A router mount, GET /.well-known/agent.json returning AgentCard (name="Customer Agent", version="1.0.0", description="Quản lý khách hàng: tìm kiếm, tạo mới, cập nhật thông tin, xem lịch sử mua.", skills=[lookup_customer, create_customer, update_customer], a2a_endpoint="http://customer-agent:8004/a2a/tasks"), GET /health, lifespan (asyncpg pool + MemoryService init, Redis init)
- [X] T136 [US12] Update docker-compose.yml: add customer-agent service (build: ./customer_agent, port 8004:8004, env REDIS_URL/TOOL_REGISTRY_URL/POSTGRES_DSN/AGENT_SEED_URLS/OPENAI_API_KEY, health check GET /health); update orchestrator service AGENT_SEED_URLS to include http://customer-agent:8004; update .env.example: add CUSTOMER_AGENT_URL=http://localhost:8004
- [X] T137 [P] [US12] Update orchestrator/prompts/intent_classify_v1.md: add "customer" intent class with 4 few-shot examples (tìm khách, thêm khách mới, cập nhật SĐT, xem lịch sử mua); update orchestrator/prompts/plan_v1.md: add customer-agent row to agent_label mapping table (customer-agent → "Quản Lý Khách Hàng") and add a few-shot example routing step with skill=lookup_customer; update CLAUDE.md between managed markers with Customer Agent (port 8004) service entry

**Checkpoint**: Customer Agent functional — all three skills (lookup/create/update) complete via A2A; HITL gate pauses create/update before Tool Registry mutation; `contact_alias` memory stored after lookup and resolved on second call (memory_hit=True); AgentRegistry discovers customer-agent within 60s (SC-024)

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Eval harness, observability, CLAUDE.md, security audit

- [x] T067 [P] Create evals/runner.py: async YAML test case loader, run eval cases against live endpoints, calculate accuracy/pass-rate per category, print summary table
- [x] T068 [P] Create evals/cases/intent_classification.yaml: 20 cases (≥ 6 order, ≥ 6 bi_query, ≥ 4 chitchat, ≥ 4 edge/ambiguous)
- [x] T069 [P] Create evals/cases/entity_extraction.yaml: 20 Vietnamese NL order inputs with expected entities (customer_name, table_number, items list with quantities)
- [x] T070 [P] Create evals/cases/nl2sql.yaml: 20 BI queries with expected SQL structure or result column names
- [x] T071 [P] Create evals/cases/e2e_order.yaml: 20 multi-turn order conversations with expected order_code in final completed step
- [x] T072 [P] Add structured JSON logging middleware to orchestrator/main.py, order_agent/main.py, bi_agent/main.py: log request start/end with agent_id, agent_role, trace_id, tenant_id, action, status, duration_ms; NEVER log Authorization header value
- [x] T073 Update CLAUDE.md between managed markers with entries for Orchestrator (port 8000, main.py, graph.py), Order Agent (port 8002), and BI Agent (port 8003) services

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately; all 4 tasks parallel
- **Phase 2 (Foundational)**: Depends on Phase 1 — BLOCKS all user stories
- **Phase 3 (US5 — A2A)**: Depends on Phase 2 — BLOCKS US1/US2/US3 which all rely on A2A
- **Phase 4 (US1 — Intent Routing)**: Depends on Phase 3 (A2A client + agent stubs)
- **Phase 4b (US7 — Plan Visibility)**: Depends on Phase 4 (plan.py, a2a_dispatch.py exist); can run in parallel with Phases 5–6
- **Phase 5 (US2 — Order Creation)**: Depends on Phase 3 (Order Agent A2A server); can run in parallel with Phase 4b
- **Phase 6 (US3 — BI Queries)**: Depends on Phase 3; can run in parallel with Phases 4b and 5
- **Phase 7 (US4 — Multi-Turn)**: Depends on Phases 4 + 5 (extends both with checkpointer)
- **Phase 7b (US8 — Domain Agent Memory)**: Depends on Phase 4b (002_agent_memory.sql references plans table via FK); depends on Phases 5 + 6 (extends order_agent and bi_agent a2a_server); can run in parallel with Phase 7
- **Phase 8b (US9 — Orchestrator Memory)**: Depends on Phase 4b (plans table must exist for find_similar_plans); depends on Phase 4 (plan.py, main.py to extend); can run in parallel with Phase 7b
- **Phase 8c (US10 — HITL)**: Depends on Phase 7b (MemoryAwareReActLoop in order_agent/core/react_loop.py and bi_agent/core/react_loop.py must exist to add _execute_tool gate); can run in parallel with Phase 8b (different files)
- **Phase 8d (US11 — Dispatch Engine)**: Depends on Phase 4b (PlanService, A2A dispatch loop exist); depends on Phase 8c (MemoryAwareReActLoop.run() signature must exist before adding payload param); T120 (models) can run in parallel with 8c; T124–T127 run after 8c MemoryAwareReActLoop is in place
- **Phase 8e (US12 — Customer Agent)**: Depends on Phase 7b (MemoryService shared lib + MemoryAwareReActLoop pattern established); depends on Phase 8c (HITL gate pattern from react_loop.py to reuse); T130–T132 (models, prompt, tools.yaml) can run in parallel with 8d; T133 (react_loop) depends on T130–T132; T134–T135 (a2a_server, main.py) run after T133; T136–T137 (docker-compose, prompts update) run after T135
- **Phase 8 (Polish)**: Depends on all previous phases

### User Story Dependencies

- **US5 (A2A) → US1, US2, US3**: A2A backbone must exist before agents can communicate
- **US1 (Intent Routing) → US4 (Multi-Turn), US7 (Plan Visibility)**: plan.py and a2a_dispatch.py must exist before US7 can extend them
- **US2 (Order Creation) → US4 (Multi-Turn)**: Confirmation interrupt extends US2's graph
- **US7 (Plan Visibility) → US8 (Domain Agent Memory)**: 002_agent_memory.sql has FK agent_task_history.plan_id → plans; US8 also extends US2 + US3 a2a_server files
- **US7 (Plan Visibility) → US9 (Orchestrator Memory)**: find_similar_plans() queries the plans table; 003_orchestrator_memory.sql depends on plans table existing
- **US1 (Intent Routing) → US9 (Orchestrator Memory)**: OrchestratorMemoryService is wired into plan.py and main.py established in US1
- **US8 (Domain Agent Memory) → US10 (HITL)**: MemoryAwareReActLoop in react_loop.py must exist before adding _execute_tool HITL gate and resume_after_hitl()
- **US10 (HITL) → US11 (Dispatch Engine)**: MemoryAwareReActLoop.run() must be stable before adding payload param and build_agent_system_prompt(); A2A dispatch loop must exist before replacing with DispatchEngine
- **US7 (Plan Visibility) → US11 (Dispatch Engine)**: DispatchEngine.execute() takes plan_service param and calls link_task/sync_from_task internally
- **US8 (Domain Agent Memory) → US12 (Customer Agent)**: MemoryService shared lib and MemoryAwareReActLoop pattern (order/bi variants) must exist before building Customer Agent variant; contact_alias/customer_profile types reuse same agent_memory table (migration 002)
- **US10 (HITL) → US12 (Customer Agent)**: HITL gate pattern (_execute_tool, _generate_confirm_message, resume_after_hitl) established in order/bi react_loop.py is copied to Customer Agent — US10 must be stable first
- **US5 (A2A) → US12 (Customer Agent)**: Customer Agent is a new Domain Agent using the same A2A skeleton established in US5

### Within Each Phase

1. Contract/integration tests first (must FAIL before implementation)
2. Models and pure functions (parallel within story)
3. Prompts (parallel within story)
4. Node implementations (sequential per graph flow dependency)
5. Graph assembly (after all nodes done)
6. A2A server integration (after graph is built)

---

## Parallel Execution Examples

### Phase 2 — Run all foundational tasks simultaneously

```
T005: shared/a2a/models.py
T006: shared/llm_client.py
T007: shared/a2a/server.py
T008: shared/a2a/client.py
T009: orchestrator/main.py
T010: order_agent/main.py
T011: bi_agent/main.py
T012: tests/conftest.py
```

### Phase 4b (US7) — Parallel setup before sequential implementation

```
Parallel: T079 (contract test), T080 (integration test), T081 (models), T088 (data-model.md)

Sequential (after T081 done):
  T082 (migration) → T083 (PlanService) → T084 (prompt) → T085 (plan.py) → T086 (dispatch.py) → T087 (main.py)
```

### Phase 5 (US2) — Parallel setup before sequential node work

```
Parallel: T031 (contract test), T032 (integration test), T033 (vn_utils tests),
          T034 (matcher tests), T035 (models), T036 (vn_utils), T037 (prompts)

Sequential (after T035–T038 done):
  T038 → T039 → T040 → T041 → T042 → T043 → T044 → T045 → T046
```

### Phase 6 (US3) — Parallel setup before sequential node work

```
Parallel: T047 (contract test), T048 (integration test), T049 (unit tests),
          T050 (models), T051 (bi_schema.yaml), T052 (nl2sql prompt), T053 (format prompt)

Sequential (after T050–T053 done):
  T054 → T055 → T056 → T057 → T058 → T059 → T060
```

### Phase 7b (US8) — Parallel setup before sequential wiring

```
Parallel: T089 (integration tests), T090 (unit tests), T093 (order react_loop), T094 (bi react_loop)

Sequential (after T089–T090 written, T093–T094 implemented):
  T091 (migration) → T092 (MemoryService) → T095 (order main.py) → T096 (bi main.py)
  → T097 (order a2a_server) → T098 (bi a2a_server)
```

### Phase 8b (US9) — Parallel tests + llm_client before sequential wiring

```
Parallel: T099 (integration tests), T100 (unit tests), T103 (llm_client.py)

Sequential (after T099–T100 written):
  T101 (migration 003) → T102 (OrchestratorMemoryService) → T104 (plan.py) → T105 (plan_v1.md)
  → T106 (main.py lifespan) → T107 (graph.py threading)
```

### Phase 8c (US10) — Parallel tests + config before sequential react_loop work

```
Parallel: T108 (integration tests), T109 (unit tests), T110 (tools.yaml), T111 (llm_client.py)

Sequential (after T108–T109 written, T110–T111 done):
  T112 (HITL helpers) → T113 (_execute_tool gate) → T114 (resume_after_hitl) → T115 (order a2a_server)

Parallel with T115: T116 (bi react_loop) → T117 (bi a2a_server)
```

### Phase 8d (US11) — Parallel models + tests before sequential DispatchEngine wiring

```
Parallel: T118 (integration tests), T119 (unit tests), T120 (models update)

Sequential (after T118–T119 written, T120 done):
  T121 (DispatchEngine) → T122 (plan_v1.md) → T123 (a2a_dispatch.py)

Parallel with T123: T124 (order react_loop) + T125 (bi react_loop)
  After T124: T126 (order a2a_server)
  After T125: T127 (bi a2a_server)
```

### Phase 8e (US12) — Parallel scaffold before sequential react_loop + server wiring

```
Parallel: T128 (contract tests), T129 (integration tests), T130 (models), T131 (prompt), T132 (tools.yaml)

Sequential (after T128–T129 written, T130–T132 done):
  T133 (react_loop) → T134 (a2a_server) → T135 (main.py)

Sequential (after T135 done):
  T136 (docker-compose + env)

Parallel with T136: T137 (intent_classify prompt + plan_v1.md + CLAUDE.md)
```

---

## Implementation Strategy

### MVP First (US5 + US1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational
3. Complete Phase 3: US5 (A2A backbone)
4. Complete Phase 4: US1 (Intent routing with agent stubs)
5. **STOP and VALIDATE**: POST /chat routes correctly via A2A stubs
6. Demo intent classification accuracy against 20-case eval

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US5 → A2A backbone → Domain Agent endpoints accept tasks
3. US1 → Orchestrator routes via A2A → MVP demo with stubs
4. US7 → Plan Visibility → Real-time progress UI enabled (runs in parallel with US2/US3)
5. US2 → Real Order Agent → End-to-end order creation
6. US3 → Real BI Agent → Business intelligence queries
7. US4 → Multi-turn → Persistent order confirmation flow
8. US8 → Domain Agent Memory → Agents learn from past tasks (depends on US7 for plans FK, US2/US3 for a2a_server)
9. US9 → Orchestrator Memory → Orchestrator learns routing strategies at meta-level (depends on US7 for plans table, US1 for plan.py/main.py)
10. US10 → HITL → Every mutating tool requires explicit user confirmation before execution (depends on US8 for MemoryAwareReActLoop structure)
11. US11 → Dispatch Engine → Parallel + sequential dispatch with A2ATaskPayload context injection (depends on US10 for stable MemoryAwareReActLoop, US7 for PlanService)
12. US12 → Customer Agent → Dedicated customer lifecycle service; alias memory recall; HITL on create/update (depends on US8 for MemoryService + MemoryAwareReActLoop pattern, US10 for HITL gate, US5 for A2A skeleton)

### Parallel Team Strategy

With multiple developers after Phase 3 (US5) completes:

- Developer A: US1 (Phases 4 — Orchestrator)
- Developer B: US2 (Phase 5 — Order Agent)
- Developer C: US3 (Phase 6 — BI Agent)

All three stories are independently testable once the A2A backbone exists.

---

## Task Summary

| Phase | Purpose | Tasks | Story |
|-------|---------|-------|-------|
| Phase 1 | Setup | T001–T004 | — |
| Phase 2 | Foundational | T005–T012 | — |
| Phase 3 | A2A Protocol | T013–T019 | US5 |
| Phase 4 | Intent Routing + AgentRegistry | T020–T030, T074–T078 | US1 |
| Phase 4b | Plan Visibility | T079–T088 | US7 |
| Phase 5 | Order Creation | T031–T046 | US2 |
| Phase 6 | BI Queries | T047–T060 | US3 |
| Phase 7 | Multi-Turn State | T061–T064 | US4 |
| Phase 7b | Domain Agent Memory | T089–T098 | US8 |
| Phase 8b | Orchestrator Memory | T099–T107 | US9 |
| Phase 8c | HITL Confirmation | T108–T117 | US10 |
| Phase 8d | Dispatch Engine + A2ATaskPayload | T118–T127 | US11 |
| Phase 8e | Customer Agent | T128–T137 | US12 |
| Phase 8 | Polish | T067–T073 | — |

**Total**: 135 tasks across 14 phases (T074–T078: AgentRegistry; T079–T088: Plan Visibility / US7; T089–T098: Domain Agent Memory / US8; T099–T107: Orchestrator Memory / US9; T108–T117: HITL / US10; T118–T127: Dispatch Engine + A2ATaskPayload / US11; T128–T137: Customer Agent / US12)

---

## Notes

- All [P] tasks within a phase target different files — safe to execute concurrently
- Test tasks (T013–T015, T020–T021, T031–T034, T047–T049, T089–T090) MUST be written first and MUST FAIL before implementation begins (Constitution §Test-First)
- Token safety: verify Authorization header never appears in any log during T072; add negative assertion to T014
- Atomic FAISS index refresh (T038): build into temp object, then swap reference — never partially update live index
- LangGraph resume pattern (T063–T064): `await graph.ainvoke(None, {"configurable": {"thread_id": tid}})` — None input signals resume from checkpoint
- PII safety (T092, T097–T098): `input_summary` must sanitise raw customer names — store `"order for customer_id=cust_123"` not `"order for Lâm"`. Assert with regex in T090.
- Best-effort learning (T093–T094): `_store_learnings` wraps `memory.store()` in `try/except Exception: pass` — never re-raises; extraction failure must not affect task outcome
- 002_agent_memory.sql (T091) depends on `plans` table existing — run after 001_plans.sql; both migrations are idempotent `CREATE TABLE IF NOT EXISTS`
- 003_orchestrator_memory.sql (T101) is independent of agent_memory but must run after plans table exists (find_similar_plans queries it); all three migrations are idempotent
- Fire-and-forget extraction (T104): use `asyncio.create_task(_fire_and_forget_extract(...))` after `create_plan()` — wrap entire task body in `try/except Exception: logger.warning(...)` — never re-raises
- OrchestratorMemoryService (T102): follows same GREATEST semantics and graceful-degradation pattern as MemoryService — when db=None all methods return [] / no-op
- Plan prompt injection (T104–T105): when routing_memory and similar_plans are empty (new tenant, no history), render empty strings — GPT-4o sees clean prompt with no ghost sections
- HITL tool gate (T113): read `requires_confirmation` from ToolRegistryClient tool definition before every tool call in _execute_tool; missing field or False = execute immediately; True = pause and return __hitl__ dict
- HITL continuation routing (T115, T117): distinguish HITL pause from order-preview pause by `task.state["hitl_pending"]` boolean flag — HITL routes to resume_after_hitl(), order-preview routes to LangGraph graph resume
- A2ATaskPayload backward compat (T126, T127): wrap `A2ATaskPayload.model_validate(task.params)` in `try/except ValidationError` — old-format tasks (plain dict without task_id UUID) fall back to `payload=None` so existing tests continue to pass
- DispatchEngine round loop (T121): a "round" = one call to asyncio.gather over all ready steps; repeat until `len(results) == len(steps)` or no new steps become ready (deadlock detection → fail remaining steps); max rounds = len(steps) to prevent infinite loop
- depends_on uses agent names (T120, T121, T122): `PlanStep.depends_on: list[str]` contains agent names (e.g. `["order-agent"]`), NOT step_ids — DispatchEngine checks `all(dep in results for dep in step.depends_on)`
- build_agent_system_prompt fallback (T124, T125): when payload is None (backward compat or plain task), use base system prompt only — no crash; all sections guarded with `if payload:` checks
- conversation_history in payload (T121): populated from `session.get_last_n_turns(6)` — each turn is `{"role": "user"|"assistant", "content": str}`; Domain Agent react_loop does NOT need to inject these into LangGraph state (session history already handled by LangGraph checkpointer); they are for context-only prompt injection via build_agent_system_prompt
- resume_after_hitl scope_change branch (T114): return value propagates up to a2a_server which sets A2A task status to completed with output={"__scope_change__": True, "new_request": ...}; Orchestrator detects signal and re-routes request as a new top-level intent
- No explicit replan gate needed for HITL modify/cancel branches — injecting user feedback into messages causes the LLM to re-reason automatically via the existing ReAct loop (Workplan §1.7 note)
- Customer Agent memory types (T133): `contact_alias` key = spoken alias text (e.g. "anh Lâm"), content = `{"customer_id": "...", "full_name": "...", "phone": "..."}` — store with confidence=0.9 after each successful lookup; `customer_profile` key = customer_id, content = VIP/purchase history summary; `lookup_pattern` key = tenant_id, content = most effective lookup strategy
- Customer Agent HITL (T133, T134): `customer__create_customer` and `customer__update_customer` both require `requires_confirmation: true` in tools.yaml — same `_execute_tool` gate pattern as order/bi agents; T132 must add `impact_template` to both tools before T133 can test HITL trigger
- Customer Agent memory_hit flag (T133, T134): when `contact_alias` resolves the customer_id from memory, set `memory_hit=True` in CustomerLookupResult and skip calling `customer__get_customers` — this is the key SC-024 assertion
- Customer Agent a2a_server continuation (T134): same HITL continuation pattern as order_agent/a2a_server.py — check `redis.get(f"hitl:{original_task_id}")` to detect pending HITL gate; route to `resume_after_hitl()` if found
- docker-compose AGENT_SEED_URLS (T136): orchestrator service env must include `http://customer-agent:8004` alongside existing order/bi URLs; AgentRegistry will auto-discover the new agent card within ≤60s of startup
- Commit after each completed task or logical group; stop at each **Checkpoint** to validate independently before proceeding

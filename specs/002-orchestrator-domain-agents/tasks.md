# Tasks: Orchestrator and Domain Agents

**Input**: Design documents from `/specs/002-orchestrator-domain-agents/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: Which user story this task belongs to (US1–US6)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Service directories, dependency declarations, Docker Compose additions, environment template

- [x] T001 Create service directories: orchestrator/, order_agent/, bi_agent/, shared/a2a/, tests/002-orchestrator-domain-agents/{contract,integration,unit}/
- [x] T002 [P] Add Python dependencies to pyproject.toml or requirements files: langgraph>=0.2, langgraph-checkpoint-redis>=0.0.6, sentence-transformers, faiss-cpu, faster-whisper, asyncpg, langchain-openai, httpx, respx
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

**Checkpoint**: Intent routing fully functional — POST /chat classifies and routes correctly with session tracking

---

## Phase 5: User Story 2 — Order Creation via Natural Language (Priority: P1)

**Goal**: Order Agent extracts Vietnamese order entities, matches products via FAISS, confirms with user, and submits to KiotViet via Tool Registry.

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

**Checkpoint**: End-to-end order creation works — NL input → FAISS product match → preview → confirm → KiotViet order

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

## Phase 8: User Story 6 — Voice Input (Priority: P2)

**Goal**: Staff can upload audio files; system transcribes and routes through identical pipeline as POST /chat.

**Independent Test**: Upload 10 Vietnamese audio clips. Verify ≥ 9/10 WER < 15%, each routes to correct agent, response includes transcription field.

- [x] T065 [US6] Implement orchestrator/stt.py: WhisperSTT class using faster-whisper WhisperModel("large-v3", compute_type="int8", download_root="/models/whisper"), transcribe_audio(audio_path) in asyncio.run_in_executor(None, fn), vad_filter=True, language="vi", join segments, strip whitespace
- [x] T066 [US6] Implement POST /voice endpoint in orchestrator/main.py: multipart UploadFile + optional session_id form field, normalize to 16kHz mono WAV via ffmpeg subprocess, reject > 60s with 422 AUDIO_TOO_LONG, call stt.transcribe_audio(), route transcription through chat pipeline, return VoiceResponse (session_id, transcription, reply, intent, trace_id, metadata.transcription_duration_ms) per contracts/voice.json

**Checkpoint**: Voice input works — audio file → transcription → same pipeline as POST /chat

---

## Phase 9: Polish & Cross-Cutting Concerns

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
- **Phase 5 (US2 — Order Creation)**: Depends on Phase 3 (Order Agent A2A server); can run in parallel with Phase 4 (US1)
- **Phase 6 (US3 — BI Queries)**: Depends on Phase 3; can run in parallel with Phases 4 and 5
- **Phase 7 (US4 — Multi-Turn)**: Depends on Phases 4 + 5 (extends both with checkpointer)
- **Phase 8 (US6 — Voice)**: Depends on Phase 4 (uses POST /chat pipeline)
- **Phase 9 (Polish)**: Depends on all previous phases

### User Story Dependencies

- **US5 (A2A) → US1, US2, US3**: A2A backbone must exist before agents can communicate
- **US1 (Intent Routing) → US4 (Multi-Turn)**: Session management extends US1's graph
- **US2 (Order Creation) → US4 (Multi-Turn)**: Confirmation interrupt extends US2's graph
- **US1 (Intent Routing) → US6 (Voice)**: Voice endpoint reuses /chat pipeline from US1

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
4. US2 → Real Order Agent → End-to-end order creation
5. US3 → Real BI Agent → Business intelligence queries
6. US4 → Multi-turn → Persistent order confirmation flow
7. US6 → Voice → Audio input pipeline

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
| Phase 4 | Intent Routing | T020–T030 | US1 |
| Phase 5 | Order Creation | T031–T046 | US2 |
| Phase 6 | BI Queries | T047–T060 | US3 |
| Phase 7 | Multi-Turn State | T061–T064 | US4 |
| Phase 8 | Voice Input | T065–T066 | US6 |
| Phase 9 | Polish | T067–T073 | — |

**Total**: 73 tasks across 9 phases

---

## Notes

- All [P] tasks within a phase target different files — safe to execute concurrently
- Test tasks (T013–T015, T020–T021, T031–T034, T047–T049) MUST be written first and MUST FAIL before implementation begins (Constitution §Test-First)
- Token safety: verify Authorization header never appears in any log during T072; add negative assertion to T014
- Atomic FAISS index refresh (T038): build into temp object, then swap reference — never partially update live index
- LangGraph resume pattern (T063–T064): `await graph.ainvoke(None, {"configurable": {"thread_id": tid}})` — None input signals resume from checkpoint
- Commit after each completed task or logical group; stop at each **Checkpoint** to validate independently before proceeding

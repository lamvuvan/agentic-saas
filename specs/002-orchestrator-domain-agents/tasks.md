# Tasks: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents`
**Input**: [plan.md](plan.md) · [spec.md](spec.md) · [data-model.md](data-model.md) · [contracts/](contracts/) · [research.md](research.md)
**Updated**: 2026-03-18 — Added LangGraph StateGraph tasks (FR-049–FR-053): `StateGraph` compilation, `interrupt_before` HITL, `AsyncRedisSaver` checkpointing, `OrchestratorState`/`OrderAgentState`/`BIAgentState`/`CustomerAgentState` TypedDicts

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Parallelizable — different files, no dependency on incomplete tasks
- **[Story]**: Maps to user story in spec.md (US1–US12)
- Exact file paths included in every task

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project structure, Docker Compose, environment configuration, linting

- [ ] T001 Create `docker-compose.yml` with 7 services: `orchestrator` (8000), `tool-registry` (8001), `order-agent` (8002), `bi-agent` (8003), `customer-agent` (8004), `redis`, `postgres`
- [ ] T002 [P] Create `config/.env.example` with all required env vars: `OPENAI_API_KEY`, `REDIS_URL`, `DATABASE_URL`, `API_BASE`, `TOOL_REGISTRY_URL`, `AGENT_SEED_URLS`, `OPENAI_MODEL_FAST=gpt-4o-mini`, `OPENAI_MODEL_SMART=gpt-4o`
- [ ] T003 [P] Create `pyproject.toml` at repo root with `ruff` config (line-length=120, target-version=py312) and per-service `requirements.txt` files listing pinned versions: `langgraph>=0.2.0`, `langgraph-checkpoint-redis>=0.0.6`, `redis>=4.6`, `fastapi>=0.111`, `pydantic>=2.0`, `httpx`, `asyncpg`, `openai`
- [ ] T004 [P] Create `Makefile` with targets: `dev` (docker compose up), `test` (pytest), `lint` (ruff check), `format` (ruff format), `eval-all` (python -m evals.runner --all)
- [ ] T005 [P] Initialize Python package structure: `orchestrator/__init__.py`, `order_agent/__init__.py`, `bi_agent/__init__.py`, `customer_agent/__init__.py`, `shared/__init__.py`, `evals/__init__.py` — create empty `__init__.py` files for all subdirectories per plan.md structure

**Checkpoint**: Repository scaffolded — all services have package structure, Docker Compose defined, linting configured

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared libraries, auth, LLM client, A2A models, DB/Redis setup, migrations — MUST complete before ANY user story

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete

- [ ] T006 Implement `shared/auth_context.py`: `ContextVar[str]` for `_token` and `_tenant_id`; `set_auth(token, tenant_id)`, `get_token() -> str`, `get_tenant_id() -> str`; `AuthForwardMiddleware` (Starlette `BaseHTTPMiddleware`) that reads `Authorization: Bearer ...` header and calls `set_auth()` on each request — NEVER logs or stores token value
- [ ] T007 [P] Implement `shared/llm_client.py`: `select_model(task_type: str) -> str` routing table (`intent_classify|entity_extract|product_rerank|response_format|confirm_message|extract_learnings` → `gpt-4o-mini`; `orchestrator_plan|nl2sql` → `gpt-4o`); `chat_completion_async(messages, system, task_type, response_schema=None) -> str` with 3-attempt exponential backoff; structured output via `response_format={"type": "json_schema", "json_schema": {...}}` when `response_schema` provided; token usage tracking per `get_tenant_id()`
- [ ] T008 [P] Implement `shared/a2a_models.py`: Pydantic v2 models — `A2ATaskPayload` (4 groups: identity fields, intent fields, `conversation_history: list[dict]`, `dependency_results: dict[str, dict]`); `TaskStatus` enum (`submitted|working|completed|failed|timeout|input-required`); `A2ATask` (with `input_request: str | None`); `A2AResult` (`output`, `reasoning_summary: str` required per Constitution, `confidence: float | None`, `tool_calls: list[str]`); `AgentCard` (with `role: Literal["orchestrator","domain"]`, `domain: str | None`, `slo`)
- [ ] T009 [P] Implement `shared/tool_registry_client.py`: `ToolRegistryClient(base_url, http_client)` with `get_tools(namespace: str) -> list[dict]`, `execute(tool_name: str, args: dict) -> dict`, `get_definition(tool_name: str) -> dict` — all calls use `httpx.AsyncClient`, forward Bearer token via `get_token()` in `Authorization` header
- [ ] T010 [P] Implement `shared/memory_service.py`: `MemoryService(db: asyncpg.Pool | None, agent_name: str)` with `retrieve(tenant_id, query_context, limit=5) -> list[dict]` (ORDER BY confidence DESC, usage_count DESC, last_used_at DESC; bump usage_count on retrieval), `store(tenant_id, memory_type, key, content, confidence=0.8)` (UPSERT with GREATEST semantics), `find_similar_tasks(tenant_id, skill, input_summary, limit=3) -> list[dict]`, `record_task(tenant_id, plan_id, skill, input_summary, outcome, key_decisions, learnings, duration_ms)` — all methods silently return empty/None when `db is None`
- [ ] T011 Create PostgreSQL migration files: `orchestrator/migrations/001_plans.sql` (CREATE TABLE `plans` + `plan_sub_goals` with indexes `idx_plans_session`, `idx_sub_goals_plan`, `idx_sub_goals_task`); `orchestrator/migrations/002_agent_memory.sql` (CREATE TABLE `agent_memory` with unique constraint `(agent_name,tenant_id,memory_type,key)` + `agent_task_history` with indexes); `orchestrator/migrations/003_orchestrator_memory.sql` (CREATE TABLE `orchestrator_memory` with UNIQUE INDEX `idx_orch_mem_key(tenant_id,memory_type,key)`)
- [ ] T012 [P] Implement `shared/db.py`: `async def create_asyncpg_pool(dsn: str | None) -> asyncpg.Pool | None` — returns None when dsn is None/empty (graceful degradation for memory features); `async def run_migrations(pool, migrations_dir)` — executes all `.sql` files in order at startup
- [ ] T013 [P] Implement `shared/redis_client.py`: `create_redis_pool(url: str) -> redis.asyncio.ConnectionPool`; `get_redis_client(pool) -> redis.asyncio.Redis` — used by both `AsyncRedisSaver` and A2A task store hash operations
- [ ] T014 Populate `config/tools.yaml` with all tool entries: `customer__get_customers` (namespace: customer, read-only), `customer__create_customer` (`requires_confirmation: true`, `impact_template: "Tạo khách hàng mới: {name} ({phone})..."`), `customer__update_customer` (`requires_confirmation: true`), `order__create_order` (`requires_confirmation: true`, `impact_template: "Tạo đơn hàng cho {customer_name}: {item_count} món, tổng {total}đ."`), `order__update_order` (`requires_confirmation: true`), `order__cancel_order` (`requires_confirmation: true`), `bi__run_query` (namespace: bi, handler: `bi_query_handler`) — all entries matching `contracts/` schemas
- [ ] T015 [P] Write contract tests in `tests/002-orchestrator-domain-agents/contract/test_a2a_models.py`: validate `A2ATaskPayload`, `A2ATask`, `A2AResult` Pydantic models against JSON Schema in `contracts/a2a-task.json`; verify `reasoning_summary` required; verify `TaskStatus` enum values match contract
- [ ] T016 [P] Write contract tests in `tests/002-orchestrator-domain-agents/contract/test_agent_card.py`: validate `AgentCard` model against `contracts/agent-card.json`; test Orchestrator card has `role: orchestrator`; test Domain Agent cards have `role: domain` and `domain` field; test all cards have `slo.p95_latency_ms`

**Checkpoint**: All shared libraries implemented and contract-tested — auth forwarding, LLM client, A2A models, DB/Redis setup, Tool Registry client, migrations, tools.yaml ready

---

## Phase 3: User Story 5 — Structured A2A Task Delegation (Priority: P1) 🎯

**Goal**: Domain Agents expose `POST /a2a/tasks` + `GET /a2a/tasks/{id}` with correct `submitted → working → completed` lifecycle; Orchestrator polls results.

**Independent Test**: Submit task directly to `http://localhost:8002/a2a/tasks` with stub skill; poll `GET /a2a/tasks/{task_id}` every 500ms; verify lifecycle transitions correctly; verify `A2AResult.reasoning_summary` present in response.

- [ ] T017 Implement `order_agent/a2a_server.py`: `POST /a2a/tasks` FastAPI router — validate `skill` + `params`, generate UUID `task_id`, store Redis hash `a2a:task:{task_id}` with `status=submitted`, spawn `asyncio.create_task(_run_task(task_id, payload))`, return `{task_id, status: "submitted"}` within 200ms; `GET /a2a/tasks/{task_id}` reads Redis hash and returns full `A2ATask` JSON; 404 if key expired or missing
- [ ] T018 [P] Implement `bi_agent/a2a_server.py`: same `POST /a2a/tasks` + `GET /a2a/tasks/{id}` pattern; `_run_task()` invokes BI Agent graph via `compiled.ainvoke()`; no HITL interrupt for BI; task TTL 1 hour after terminal status
- [ ] T019 [P] Implement `customer_agent/a2a_server.py`: same pattern; include `POST /a2a/tasks/{id}/resume` — reads `body.user_response`, calls `compiled.ainvoke(Command(resume={"user_confirmation": body.user_response}), config={"configurable": {"thread_id": task_id}})`, updates task status in Redis
- [ ] T020 Implement `order_agent/a2a_server.py` HITL resume: `POST /a2a/tasks/{id}/resume` — same `Command(resume=...)` pattern; after resume `ainvoke` completes, update Redis hash with final `status` and `result`; set 1-hour TTL on completed/failed task
- [ ] T021 [P] Wire Domain Agent FastAPI apps: `order_agent/main.py`, `bi_agent/main.py`, `customer_agent/main.py` — each with `@asynccontextmanager lifespan` (create Redis pool, `AsyncRedisSaver.from_conn_string(REDIS_URL)`, DB pool, `ToolRegistryClient`), include A2A router, `GET /health`, `GET /.well-known/agent.json` returning typed `AgentCard` per `contracts/agent-card.json` examples
- [ ] T022 [P] Write unit tests for A2A task store in `tests/002-orchestrator-domain-agents/unit/test_a2a_task_store.py`: test `submitted → working → completed` Redis transitions; test 404 for unknown task_id; test `input-required` state with `input_request` field; mock Redis with `fakeredis.aioredis`
- [ ] T023 [P] Write integration test for A2A round-trip in `tests/002-orchestrator-domain-agents/integration/test_a2a_protocol.py`: submit stub task → poll → assert `completed` within 5s; assert `A2AResult` has `reasoning_summary`; assert Bearer token NOT present in Redis task hash

**Checkpoint**: A2A protocol wired on all 3 Domain Agent ports — task submission, polling, HITL resume endpoints all functional

---

## Phase 4: User Story 1 — Intent Routing (Priority: P1)

**Goal**: Orchestrator classifies messages into `order|bi|customer|chitchat|unknown` (≥ 90% accuracy) and routes to correct Domain Agent; chitchat replies directly without A2A dispatch.

**Independent Test**: Send 20 diverse messages to `POST /chat`; verify ≥ 18/20 correct `intent` in response; verify "xin chào" returns chitchat reply without A2A call; verify ambiguous message triggers clarifying question.

- [X] T024 Define `OrchestratorState(TypedDict)` in `orchestrator/graph.py`: `session_id: str`, `tenant_id: str`, `messages: Annotated[list[BaseMessage], add_messages]` (import `add_messages` from `langgraph.graph.message`), `intent: str`, `plan_id: str`, `plan: dict`, `dispatch_results: dict`, `hitl_pending: dict | None`, `final_response: str`
- [ ] T025 Implement `orchestrator/nodes/intent_classify.py`: `async def classify_intent(state: OrchestratorState, config) -> OrchestratorState` — calls `chat_completion_async(task_type="intent_classify")` with `orchestrator/prompts/intent_classify_v1.md` (5 few-shot examples covering order/bi/customer/chitchat/unknown); if `confidence < 0.72`, escalate to `gpt-4o`; returns `{**state, "intent": intent}`
- [ ] T026 [P] Implement `orchestrator/nodes/chitchat.py`: `async def chitchat(state, config) -> OrchestratorState` — GPT-4o-mini, friendly Vietnamese reply from `messages[-1]`; prompt in `orchestrator/prompts/chitchat_v1.md`; returns `{**state, "final_response": reply}`
- [ ] T027 [P] Implement `orchestrator/nodes/aggregate.py`: `async def respond(state, config) -> OrchestratorState` — GPT-4o-mini synthesizes `dispatch_results` dict into coherent Vietnamese reply; if `dispatch_results` empty (chitchat path), uses `state["final_response"]` as-is; prompt in `orchestrator/prompts/respond_v1.md`
- [ ] T028 Implement `orchestrator/core/agent_registry.py`: `AgentRegistry(seed_urls: list[str], refresh_interval=60)` with `async def start()` (first fetch + schedule `asyncio.create_task(_background_refresh())`); `async def _refresh()` (GET `{url}/.well-known/agent.json` for each URL, populate `_agents` dict, track `_healthy` set); `def build_prompt_context() -> str` (render Markdown manifest for plan prompt); reads `AGENT_SEED_URLS` comma-separated env var
- [ ] T029 [P] Implement stub `orchestrator/nodes/plan.py`: `async def plan(state, config) -> OrchestratorState` — GPT-4o single call produces `{display: {goal, sub_goals[{sequence, title, agent_name, agent_label}]}, routing: {steps: [{sequence, agent, skill, depends_on, instructions, parameters}]}}` per `PLAN_NODE_SYSTEM` prompt (with `{agent_manifest}` placeholder injected from `AgentRegistry.build_prompt_context()`); returns `{**state, "plan": plan_dict, "plan_id": str(uuid4())}`
- [ ] T030 [P] Implement stub `orchestrator/nodes/a2a_dispatch.py`: `async def dispatch(state, config) -> OrchestratorState` — basic sequential dispatch for initial wiring (DispatchEngine parallel logic added in US11); for each routing step, build `A2ATaskPayload`, call A2A endpoint, poll until done; returns `{**state, "dispatch_results": results}`
- [X] T031 Assemble Orchestrator `StateGraph` in `orchestrator/graph.py`: `graph = StateGraph(OrchestratorState)`; `graph.add_node(...)` for all 5 nodes (`classify_intent`, `plan`, `dispatch`, `chitchat`, `respond`); `graph.set_entry_point("classify_intent")`; `add_conditional_edges("classify_intent", route_after_classify, {"chitchat": "chitchat", "plan": "plan"})`; `add_edge("plan", "dispatch")`, `add_edge("dispatch", "respond")`, `add_edge("chitchat", "respond")`, `add_edge("respond", END)`; `compiled = graph.compile(checkpointer=AsyncRedisSaver.from_conn_string(REDIS_URL))`; expose `async def invoke_chat(session_id, tenant_id, message) -> str`
- [ ] T032 Implement `orchestrator/session.py`: `SessionStore(redis_client)` with `get_or_create(session_id) -> dict`, `add_turn(session_id, role, content)`, `get_history(session_id, n=6) -> list[dict]` — Redis key `session:{session_id}` JSON string, TTL 1800s (30 min reset on activity), max 20 turns stored
- [ ] T033 Implement `orchestrator/main.py`: FastAPI app with `@asynccontextmanager lifespan` (create Redis pool, compile graph with `AsyncRedisSaver`, create DB pool, start `AgentRegistry`); `POST /chat` (parse Bearer + optional `session_id`, call `invoke_chat`, return `{reply, session_id, intent}`); `GET /health`; `GET /.well-known/agent.json` (Orchestrator Agent Card); include `AuthForwardMiddleware`
- [ ] T034 [P] Write unit tests in `tests/002-orchestrator-domain-agents/unit/test_intent_classify.py`: test all 5 intent types with mock LLM; test confidence < 0.72 triggers gpt-4o escalation; test chitchat routing; mock `chat_completion_async` with respx
- [ ] T035 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_orchestrator_chat.py`: send order/bi/customer/chitchat messages to `POST /chat`; mock Domain Agent A2A endpoints with respx to return stub results; assert correct `intent` field; assert chitchat reply without A2A call; assert Bearer token not in logs

**Checkpoint**: Orchestrator live on port 8000 — intent classification, routing, chitchat working; `POST /chat` returns correct intent and response

---

## Phase 5: User Story 2 — Order Creation via Natural Language (Priority: P1)

**Goal**: Order Agent extracts Vietnamese order entities, matches products via FAISS, previews order, and creates it after HITL confirmation.

**Independent Test**: Submit 20 order inputs via A2A; verify ≥ 17/20 produce correct preview (right products, quantities, customer); confirm with "xác nhận" → verify `completed` with order reference.

- [ ] T036 Implement `order_agent/vn_utils.py`: pure functions `normalize_text(text: str) -> str` (unicode NFC + lowercase), `strip_honorifics(text: str) -> str` (remove: anh/chị/em/bác/cô/chú/ông/bà), `words_to_numbers(text: str) -> str` (map: một→1, hai→2, ba→3, bốn→4, năm→5, sáu→6, bảy→7, tám→8, chín→9, mười→10, mười một→11, mười hai→12), `extract_product_note(text: str) -> str | None`
- [ ] T037 Implement `order_agent/product_matcher.py`: `ProductMatcher` class — `__init__` loads `paraphrase-multilingual-MiniLM-L12-v2` model; `build_index(products: list[dict])` creates FAISS `IndexFlatIP` with L2-normalized embeddings, stores parallel `product_ids: list[str]`; `search(query: str, k=5) -> list[ProductMatch]` (cosine similarity); `atomic_refresh(new_products)` (build new index into temp, swap `self._index` reference atomically); `start_background_refresh(fetch_fn, interval=1800)` schedules `asyncio.create_task`
- [X] T038 Define `OrderAgentState(TypedDict)` in `order_agent/graph.py`: all `A2ATaskPayload` fields (`task_id`, `plan_id`, `original_message`, `instructions`, `conversation_history`, `dependency_results`), `memory_context: str`, `entities: dict`, `matched_products: list`, `customer: dict`, `order_preview: dict`, `confirm_message: str`, `user_confirmation: str`, `result: dict`
- [ ] T039 [P] Implement `order_agent/nodes/extract_entities.py`: `async def extract_entities(state, config) -> OrderAgentState` — preprocess with `vn_utils`, call GPT-4o-mini structured output `OrderEntities` schema; injects `memory_context` (vn_expression, customer_pref facts) into system prompt; prompt in `order_agent/prompts/extract_entities_v1.md`; returns `{**state, "entities": entities_dict}`
- [ ] T040 [P] Implement `order_agent/nodes/match_products.py`: `async def match_products(state, config) -> OrderAgentState` — for each `entities.items`, call `ProductMatcher.search()`; apply thresholds (≥0.85 → auto-select, 0.65–0.84 → GPT-4o-mini rerank from top-3, <0.65 → `match_status="ask_user"`); check `memory_context` for `product_alias` hits first; returns `{**state, "matched_products": matches}`
- [ ] T041 [P] Implement `order_agent/nodes/check_customer.py`: `async def check_customer(state, config) -> OrderAgentState` — call `customer__get_customers` via `ToolRegistryClient` with customer name from entities; if not found, set `customer = {"not_found": True}`; returns `{**state, "customer": customer_dict}`
- [ ] T042 Implement `order_agent/nodes/preview_order.py`: `async def preview_order(state, config) -> OrderAgentState` — build `order_preview` dict from `matched_products` + `customer`; calculate `total_estimate`; generate `confirm_message` (Vietnamese, itemized: customer name, table, items with prices, total); set `order_preview["ready"] = True` only when all products auto-matched/reranked AND customer found; returns `{**state, "order_preview": preview, "confirm_message": msg}`
- [X] T043 Implement `order_agent/nodes/hitl_confirm.py`: `async def hitl_confirm(state, config) -> OrderAgentState` — reads `state["user_confirmation"]`; classifies via GPT-4o-mini into `confirm|modify|cancel|scope_change`; for `modify`: updates `entities` with modification and returns to `extract_entities`; for `cancel`: sets `result` with cancellation message; for `scope_change`: sets `result["__scope_change__"] = True`; this node is reached AFTER the interrupt fires — `user_confirmation` is populated by `Command(resume=...)`
- [X] T044 [P] Implement `order_agent/nodes/create_order.py`: `async def create_order(state, config) -> OrderAgentState` — calls `order__create_order` via `ToolRegistryClient` with `customer_id`, `items`, `table_number`, `discount`; returns `{**state, "result": {"order_id": ..., "order_code": ...}}`
- [X] T045 [P] Implement `order_agent/nodes/done.py`: `async def done(state, config) -> OrderAgentState` — call `MemoryService.record_task(outcome="success", key_decisions=..., duration_ms=...)`; fire `asyncio.create_task(_extract_and_store_learnings(state, memory_service))` (best-effort, wrapped in `try/except`); build final `A2AResult(output=state["result"], reasoning_summary=..., tool_calls=[...])` → returns `{**state, "result": a2a_result.model_dump()}`
- [X] T046 Assemble Order Agent `StateGraph` in `order_agent/graph.py`: `add_node` for all 7 nodes; `set_entry_point("extract_entities")`; `add_edge("extract_entities", "match_products")`, `add_edge("match_products", "check_customer")`, `add_edge("check_customer", "preview_order")`; `add_conditional_edges("preview_order", should_confirm, {"hitl_confirm": "hitl_confirm", "extract_entities": "extract_entities"})`; `add_edge("hitl_confirm", "create_order")`, `add_edge("create_order", "done")`, `add_edge("done", END)`; `compiled = graph.compile(checkpointer=AsyncRedisSaver.from_conn_string(REDIS_URL), interrupt_before=["hitl_confirm"])`
- [ ] T047 Update `orchestrator/nodes/plan.py`: finalize `PLAN_NODE_SYSTEM` prompt with `PLAN_INSTRUCTIONS_GUIDE` guidance — LLM MUST generate specific `instructions` per step (scope, constraints, output format, upstream reference if `depends_on` non-empty); validate routing output has `depends_on` list and `instructions` string for each step
- [ ] T048 [P] Write unit tests for `order_agent/vn_utils.py` in `tests/002-orchestrator-domain-agents/unit/test_vn_utils.py`: 50 test cases covering number words (mười hai=12), honorific stripping (anh/chị/em/bác), text normalization, edge cases
- [ ] T049 [P] Write unit tests for `order_agent/product_matcher.py` in `tests/002-orchestrator-domain-agents/unit/test_product_matcher.py`: test FAISS index build + search; test threshold routing (auto/rerank/ask_user); test atomic refresh (no partial state); test Vietnamese product name variations
- [ ] T050 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_order_agent.py`: submit "bàn 3 cho tôi 3 bò kho bánh mì" via A2A → assert `input-required` with confirm_message containing "bàn 3" and total; resume with "xác nhận" → assert `completed` with order_code; test "thêm thêm một trứng lộn" mid-preview (modify flow)

**Checkpoint**: Order Agent fully functional — entity extraction, FAISS matching, customer lookup, multi-turn preview, HITL create order working

---

## Phase 6: User Story 3 — Business Intelligence Queries (Priority: P1)

**Goal**: BI Agent translates Vietnamese questions to SQL, executes SELECT-only queries, formats human-readable answers; ≥ 80% correctness on standard queries.

**Independent Test**: Submit 15 BI queries via Orchestrator `/chat`; verify ≥ 12/15 correct formatted answers; verify INSERT query rejected with clear error message; verify LIMIT 500 injected automatically.

- [X] T051 Define `BIAgentState(TypedDict)` in `bi_agent/graph.py`: `task_id`, `plan_id`, `original_message`, `instructions`, `conversation_history`, `dependency_results`, `memory_context: str`, `generated_sql: str`, `query_result: list`, `result: dict` — no HITL fields; no `interrupt_before` needed
- [ ] T052 [P] Implement `bi_agent/nodes/retrieve_memory.py`: `async def retrieve_memory(state, config) -> BIAgentState` — calls `MemoryService.retrieve(keyword=original_message[:50])` (sql_pattern, glossary_fix, column_alias facts); builds `memory_context` string; returns `{**state, "memory_context": context_str}`; graceful no-op when MemoryService unavailable
- [ ] T053 [P] Implement `bi_agent/nodes/nl2sql.py`: `async def nl2sql(state, config) -> BIAgentState` — inject `config/bi_schema.yaml` (table allowlist + glossary) + `memory_context` (sql_pattern/glossary_fix) into system prompt; call GPT-4o `temperature=0`; prompt in `bi_agent/prompts/nl2sql_v1.md`; returns `{**state, "generated_sql": sql}`
- [ ] T054 [P] Implement `bi_agent/nodes/safety_check.py`: `async def safety_check(state, config) -> BIAgentState` — enforce SELECT-only (reject INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE with `SafetyError`); inject `LIMIT 500` if no LIMIT clause present; log generated SQL for audit; returns `{**state, "generated_sql": safe_sql}`
- [ ] T055 [P] Implement `bi_agent/nodes/execute_query.py`: `async def execute_query(state, config) -> BIAgentState` — execute `generated_sql` via `asyncpg.Pool.fetch()`; limit rows to 500; convert `asyncpg.Record` to `list[dict]`; returns `{**state, "query_result": rows}`
- [ ] T056 [P] Implement `bi_agent/nodes/format_result.py`: `async def format_result(state, config) -> BIAgentState` — GPT-4o-mini, convert `query_result` to Vietnamese human-readable answer (1-line summary + table if > 3 rows); prompt in `bi_agent/prompts/format_v1.md`; returns `{**state, "result": {"summary": text, "rows": query_result}}`
- [X] T057 Assemble BI Agent `StateGraph` in `bi_agent/graph.py`: linear: `START → retrieve_memory → nl2sql → safety_check → execute_query → format_result → done → END`; `done` node records task history and learning extraction (same pattern as Order Agent `done.py`); `compiled = graph.compile(checkpointer=MemorySaver())` — MemorySaver (in-process, per-task, no Redis)
- [ ] T058 [P] Write unit tests for `bi_agent/nodes/safety_check.py` in `tests/002-orchestrator-domain-agents/unit/test_safety_check.py`: test SELECT allowed; test INSERT/UPDATE/DELETE/DROP rejected; test LIMIT injection (added when absent; not duplicated when present); test `LIMIT 600` reduced to 500
- [ ] T059 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_bi_agent.py`: submit "doanh thu hôm nay" → assert formatted number; submit "top 5 khách hàng tháng này" → assert ranked list; submit "DELETE FROM orders" → assert `failed` status with rejection message

**Checkpoint**: BI Agent live on port 8003 — NL2SQL + safety enforcement + formatted answers working

---

## Phase 7: User Story 10 — HITL Confirmation for Mutating Tools (Priority: P1)

**Goal**: Every `requires_confirmation: true` tool call pauses via LangGraph `interrupt_before`, presents Vietnamese confirmation, resumes only after explicit approval.

**Independent Test**: Trigger `order__create_order` — verify A2A `input_required` BEFORE tool call; resume "xác nhận" → tool executes → `completed`; resume "thôi bỏ đi" → tool NOT called → `completed` with cancellation message; read-only tool executes immediately without pause.

- [ ] T060 Implement `orchestrator/core/confirm_generator.py`: `async def generate_confirm_message(tool_name: str, args: dict, impact_template: str) -> str` — GPT-4o-mini with `CONFIRM_PROMPT` from `orchestrator/prompts/confirm_v1.md`; format: "[Hành động]. [Tác động]. Xác nhận không?"; e.g. "Tạo đơn hàng cho Lâm: 2 trứng lộn, tổng 30,000đ. Xác nhận không?"
- [X] T061 Complete `order_agent/nodes/hitl_confirm.py`: implement `_classify_hitl_response(user_confirmation: str) -> Literal["confirm","modify","cancel","scope_change"]` via GPT-4o-mini with Vietnamese-aware prompt; 4-branch return routing — confirm: proceed; modify: inject user response into entities + re-route to extract_entities; cancel: set result with cancellation msg; scope_change: set `result["__scope_change__"]=True`
- [X] T062 Verify `order_agent/a2a_server.py` `_run_task()`: after first `compiled.ainvoke()`, check if returned state has `confirm_message` populated (indicator that graph halted at interrupt); if so, update Redis hash `status=input-required`, `input_request=state["confirm_message"]`; `POST /a2a/tasks/{id}/resume` resumes with `Command(resume={"user_confirmation": body.user_response})`
- [X] T063 [P] Complete `customer_agent/a2a_server.py` same HITL pattern: `_run_task()` checks for `confirm_message` in CustomerAgentState after first `ainvoke`; `POST /a2a/tasks/{id}/resume` resumes with `Command(resume={"user_confirmation": ...})`; applies to both `create_customer` and `update_customer` branches
- [ ] T064 Verify `config/tools.yaml` has `requires_confirmation: true` and non-empty `impact_template` for all mutating tools; `requires_confirmation` absent or `false` for read-only tools; `ToolRegistryClient.get_definition()` returns these fields correctly
- [ ] T065 [P] Write unit tests in `tests/002-orchestrator-domain-agents/unit/test_hitl_classify.py`: test "xác nhận" → confirm; test "giảm xuống 2" → modify; test "thôi bỏ đi" → cancel; test "đổi sang order khác" → scope_change; test ambiguous input defaults to cancel; mock GPT-4o-mini
- [ ] T066 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_hitl_flow.py`: submit order task → assert `input_required` before `order__create_order` called → resume "xác nhận" → assert `completed` with order_code → verify tool WAS called; submit again → resume "thôi" → assert `completed` with cancellation → verify tool NOT called

**Checkpoint**: HITL safety gate enforced — no mutating tool executes without human confirmation via LangGraph interrupt/resume cycle

---

## Phase 8: User Story 11 — Parallel & Sequential Task Dispatch (Priority: P1)

**Goal**: Independent steps dispatch concurrently via `asyncio.gather`; dependent steps receive upstream `dependency_results`; Plan node generates `depends_on` + `instructions` per step.

**Independent Test**: Combined order+BI request → both A2A tasks submitted within 200ms (parallel). Sequential request with `depends_on: ["order-agent"]` → BI `A2ATaskPayload.dependency_results` contains order result.

- [ ] T067 Implement `orchestrator/core/dispatch_engine.py`: `class DispatchEngine` with `async def execute(steps: list[dict], plan_id: str, state: OrchestratorState) -> dict[str, dict]` — topological evaluation loop: `ready = [s for s in remaining if all(dep in results for dep in s.get("depends_on", []))]`; `if not ready: raise RuntimeError("Circular dependency")`; `dispatched = await asyncio.gather(*[_dispatch_one(step, ...) for step in ready])`; collect results, remove from remaining, loop until done
- [ ] T068 Implement `orchestrator/core/dispatch_engine.py` `_dispatch_one()`: build `A2ATaskPayload` with `dependency_results = {dep: results[dep] for dep in step.get("depends_on", [])}`, `original_message = state["user_message"]`, `instructions = step["instructions"]`, `conversation_history = state["messages"][-6:]`; call `a2a_client.submit(agent_url, payload)` → call `plan_service.link_task(plan_id, step["sequence"], task_id)` immediately; poll via `a2a_client.poll_until_done()` (500ms interval, 30s timeout)
- [ ] T069 Implement `orchestrator/core/a2a_client.py`: `class A2AClient` with `async def submit(agent_url: str, payload: A2ATaskPayload) -> str` (POST, return task_id); `async def poll_until_done(task_id: str, agent_url: str) -> dict` (poll every 500ms, 30s timeout → raise `AgentTimeoutError`); `async def resume(task_id: str, agent_url: str, user_response: str) -> dict`; forward Bearer token from `get_token()` on all requests
- [ ] T070 Update `orchestrator/nodes/a2a_dispatch.py` to use `DispatchEngine.execute()` replacing the stub sequential loop from T030; pass `state` to engine for `original_message` + `conversation_history` extraction
- [ ] T071 [P] Write unit tests in `tests/002-orchestrator-domain-agents/unit/test_dispatch_engine.py`: test parallel dispatch (2 steps with empty `depends_on` → `asyncio.gather` called once with both); test sequential dispatch (step 2 `depends_on=["order-agent"]` → step 2 receives step 1 result in `dependency_results`); test circular dependency raises `RuntimeError`; test upstream failure → downstream skipped with `failed` status; mock `a2a_client` with respx
- [ ] T072 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_parallel_dispatch.py`: submit "doanh thu hôm nay và tạo đơn cho Lâm" → mock both A2A endpoints → assert both POSTed within 200ms; submit "sau khi tạo đơn xong thì xem doanh thu" → assert BI `A2ATaskPayload.dependency_results` has `"order-agent"` key with order result

**Checkpoint**: DispatchEngine active — parallel dispatch for independent tasks, sequential with dependency injection for ordered tasks

---

## Phase 9: User Story 12 — Customer Management (Priority: P1)

**Goal**: Customer Agent handles `lookup_customer` (memory-backed), `create_customer` (HITL), `update_customer` (HITL); Orchestrator routes `customer` intent to it.

**Independent Test**: 10 Customer Agent tasks (lookup/create/update); ≥ 9/10 correct; second lookup of "anh Lâm" resolves from `contact_alias` memory (no Tool Registry call).

- [X] T073 Define `CustomerAgentState(TypedDict)` in `customer_agent/graph.py`: `task_id`, `plan_id`, `original_message`, `instructions`, `conversation_history`, `dependency_results`, `memory_context: str`, `action: str`, `customer_data: dict`, `confirm_message: str`, `user_confirmation: str`, `result: dict`
- [ ] T074 [P] Implement `customer_agent/nodes/retrieve_memory.py` + `customer_agent/nodes/classify_action.py`: `retrieve_memory` calls `MemoryService.retrieve()` for contact_alias/customer_profile; `classify_action` GPT-4o-mini determines `action: "lookup"|"create"|"update"` + extracts `customer_data` (name, phone, address) from `instructions + original_message`
- [ ] T075 [P] Implement `customer_agent/nodes/lookup_customer.py`: check `memory_context` for `contact_alias` hit (sets `memory_hit=True`, skip Tool Registry); else call `customer__get_customers` via `ToolRegistryClient`; on completion call `MemoryService.store(memory_type="contact_alias", key=spoken_alias, content=json.dumps(customer_record))` for future cache hits
- [ ] T076 [P] Implement `customer_agent/nodes/prepare_create.py` and `customer_agent/nodes/prepare_update.py`: build Vietnamese `confirm_message` from `customer_data` (e.g. "Tạo khách hàng mới: Hoa – 09xx. Xác nhận không?"); write to `state["confirm_message"]`
- [ ] T077 [P] Implement `customer_agent/nodes/create_customer.py` and `customer_agent/nodes/update_customer.py`: call `customer__create_customer` / `customer__update_customer` via `ToolRegistryClient`; write result to `state["result"]`
- [ ] T078 [P] Implement `customer_agent/nodes/done.py`: same pattern as Order Agent done — `MemoryService.record_task()` + best-effort `asyncio.create_task(_extract_learnings(...))` + build `A2AResult`
- [X] T079 Assemble Customer Agent `StateGraph` in `customer_agent/graph.py`: `START → retrieve_memory → classify_action`; conditional from `classify_action`: `lookup → lookup_customer → done → END`; `create → prepare_create → hitl_confirm → create_customer → done → END`; `update → prepare_update → hitl_confirm → update_customer → done → END`; `compiled = graph.compile(checkpointer=AsyncRedisSaver.from_conn_string(REDIS_URL), interrupt_before=["hitl_confirm"])`
- [ ] T080 Update `orchestrator/core/agent_registry.py` `AGENT_SEED_URLS` default to include Customer Agent (`http://customer-agent:8004`); update `orchestrator/prompts/plan_v1.md` to include `customer-agent → "Quản Lý Khách Hàng"` label mapping
- [ ] T081 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_customer_agent.py`: lookup "tìm anh Lâm" → assert customers list returned; create "thêm khách Hoa 09xx" → assert `input_required` pause → resume "xác nhận" → assert `completed`; lookup "anh Lâm" second time → assert `memory_hit=True` in result; mock Tool Registry with respx

**Checkpoint**: Customer Agent live on port 8004 — all 3 skills working with HITL for writes; contact alias memory cached after first lookup; Orchestrator routes `customer` intent correctly

---

## Phase 10: User Story 4 — Multi-Turn Conversation State (Priority: P2)

**Goal**: Session history persists across requests; users can modify in-progress orders; 30-minute idle expiry creates clean slate.

**Independent Test**: 3-turn order conversation (describe → "thêm thêm một chai nước" → confirm); verify final order contains all items without duplication; verify after 30-min idle, new message starts fresh session.

- [ ] T082 Write explicit unit tests for `OrchestratorState.messages` accumulation in `tests/002-orchestrator-domain-agents/unit/test_orchestrator_state.py`: simulate 3 sequential `compiled.ainvoke()` calls with same `thread_id=session_id`; assert `messages` list grows via `add_messages` reducer; assert no message duplication (LangGraph deduplication by message id)
- [ ] T083 [P] Write unit tests for `orchestrator/session.py` session expiry in `tests/002-orchestrator-domain-agents/unit/test_session.py`: mock Redis with `fakeredis`; assert TTL reset on each `add_turn()`; assert `get_or_create()` returns new session when key expired; assert max 20 turns stored (oldest dropped)
- [ ] T084 [P] Verify `POST /chat` `session_id` handling in `orchestrator/main.py`: if `session_id` absent → generate UUID; if present → validate exists in Redis session store (else start fresh session); assert `session_id` always returned in response
- [ ] T085 [P] Write integration test for multi-turn order in `tests/002-orchestrator-domain-agents/integration/test_multi_turn.py`: 3-turn sequence with same `session_id`; verify Order Agent receives `conversation_history` with prior turns in `A2ATaskPayload`; verify "thêm chai nước" adds to existing preview items (not new draft)

**Checkpoint**: Multi-turn conversation fully tested — LangGraph checkpointer + session store keep state across requests; expiry creates clean slate

---

## Phase 11: User Story 7 — Plan Visibility (Priority: P2)

**Goal**: `GET /plans/{session_id}/current` returns real-time plan status within 200ms; sub-goals transition `pending → running → completed` as A2A tasks complete.

**Independent Test**: Submit 2-agent request; poll `GET /plans/{session_id}/current` within 200ms; plan in DB; sub-goal status transitions visible; all `agent_name` fields absent from user-facing response (only `agent_label`).

- [ ] T086 Implement `orchestrator/core/plan_service.py`: `class PlanService(db: asyncpg.Pool)` with `async def create_plan(session_id, tenant_id, user_message, display) -> str` (INSERT to `plans` with `status=running` + INSERT all `plan_sub_goals` in same transaction, RETURN plan_id); `async def link_task(plan_id, sequence, a2a_task_id)` (UPDATE sub_goal `a2a_task_id=..., status=running, started_at=now()`); `async def sync_from_task(a2a_task_id, task_status, result_summary=None)` (UPDATE sub_goal status + `completed_at`; call `_maybe_complete_plan()`); `async def get_plan(plan_id) -> dict` (JOIN plans + plan_sub_goals)
- [ ] T087 Update `orchestrator/nodes/plan.py`: call `await plan_service.create_plan(session_id, tenant_id, user_message, plan["display"])` immediately after LLM output; store returned `plan_id` in `OrchestratorState`; this MUST happen before dispatch node runs (Constitution V compliance)
- [ ] T088 Update `orchestrator/core/dispatch_engine.py._dispatch_one()`: call `await plan_service.link_task(plan_id, step["sequence"], task_id)` immediately after `a2a_client.submit()`; call `await plan_service.sync_from_task(task_id, task.status, task.get("result"))` on each poll cycle
- [ ] T089 [P] Add plan API endpoints to `orchestrator/main.py`: `GET /plans/{session_id}/current` (fetchrow latest plan for session from PostgreSQL, call `get_plan()`); `GET /plans/{plan_id}` (direct plan lookup); both responses MUST include `agent_label` and MUST NOT include `agent_name` in user-facing sub_goal output
- [ ] T090 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_plan_visibility.py`: submit 2-agent request; assert `GET /plans/{session_id}/current` returns plan within 200ms; mock A2A tasks as async-completing stubs; assert sub_goal status transitions `pending → running → completed`; assert `agent_name` absent from API response

**Checkpoint**: Plan visibility live — frontend can poll real-time sub-goal progress with Vietnamese business descriptions

---

## Phase 12: User Story 8 — Domain Agent Memory (Priority: P2)

**Goal**: Agents accumulate `product_alias`, `sql_pattern`, `contact_alias` facts; inject into subsequent tasks; learning extraction runs best-effort after each task.

**Independent Test**: Submit order with "ba đen" → verify `product_alias` memory row in PostgreSQL; submit same order again → verify memory `hit` (no extra user prompt for alias); submit second BI query with same intent → verify `sql_pattern` in system prompt.

- [ ] T091 Complete `order_agent/nodes/done.py` learning extraction: add `EXTRACT_LEARNINGS_PROMPT` to `order_agent/prompts/extract_learnings_v1.md`; implement `async def _extract_and_store_learnings(state, memory_service)` — call GPT-4o-mini, parse JSON array `[{memory_type, key, content, confidence}]`, call `memory_service.store()` for each; entire function wrapped in `try/except Exception: pass` (never blocks task)
- [ ] T092 [P] Complete `bi_agent/nodes/done.py` learning extraction: same pattern with `bi_agent/prompts/extract_learnings_v1.md` — extracts `sql_pattern`, `glossary_fix`, `column_alias` facts; `MemoryService.record_task()` with sanitized `input_summary` (no PII)
- [ ] T093 [P] Complete `customer_agent/nodes/done.py` learning extraction: extract `contact_alias`, `customer_profile`, `lookup_pattern` facts; `lookup_customer` node must already call `MemoryService.store(memory_type="contact_alias", ...)` on successful lookup — verify this wiring from T075
- [ ] T094 Verify `shared/memory_service.py` `retrieve()` bumps `usage_count` and `last_used_at` on each retrieval (T010 stub); add test in `tests/002-orchestrator-domain-agents/unit/test_memory_service.py`: verify GREATEST semantics on upsert (lower confidence not overwrite higher); verify `retrieve` ordering (confidence DESC, usage_count DESC)
- [ ] T095 [P] Write integration test for memory accumulation in `tests/002-orchestrator-domain-agents/integration/test_agent_memory.py`: submit Order Agent task with "ba đen" → assert `agent_memory` row for `product_alias` key="ba đen"; submit same task again → assert `memory_context` string passed to `extract_entities` node contains the alias fact; assert `usage_count` incremented

**Checkpoint**: Domain Agent memory active — product aliases, SQL patterns, customer aliases persist and speed up subsequent identical requests

---

## Phase 13: User Story 9 — Orchestrator Memory (Priority: P2)

**Goal**: Orchestrator accumulates `routing_pattern` and `plan_template` from completed plans; injects into Plan node prompt for subsequent requests.

**Independent Test**: Complete a combined order+BI plan → verify `orchestrator_memory` row inserted; on next similar request verify `routing_memory` block in Plan node system prompt (visible in structured log).

- [ ] T096 Implement `orchestrator/core/orchestrator_memory.py`: `class OrchestratorMemoryService(db: asyncpg.Pool | None)` with `async def retrieve_patterns(tenant_id, intent_class, request_summary, limit=4) -> list[dict]` (ilike query on key/content, ORDER BY confidence DESC); `async def store_pattern(tenant_id, memory_type, key, content, confidence=0.8)` (UPSERT with `GREATEST` semantics); `async def find_similar_plans(tenant_id, user_message, limit=3) -> list[dict]` (JOIN plans + plan_sub_goals WHERE status='completed'); `async def extract_and_store(tenant_id, plan, sub_goals)` (GPT-4o-mini with `ROUTING_LEARNINGS_PROMPT` from `orchestrator/prompts/routing_learnings_v1.md`, best-effort)
- [ ] T097 Update `orchestrator/nodes/plan.py`: before LLM call, `patterns = await orch_memory.retrieve_patterns(...)` and `similar = await orch_memory.find_similar_plans(...)`; inject into `ORCHESTRATOR_SYSTEM_TEMPLATE` via `{routing_memory}` and `{similar_plans}` placeholders; if `OrchestratorMemoryService is None`, use empty strings for placeholders
- [ ] T098 Update `orchestrator/core/plan_service.py._maybe_complete_plan()`: when all sub_goals terminal, fire `asyncio.create_task(orch_memory.extract_and_store(tenant_id, plan, sub_goals))` — non-blocking, never raises
- [ ] T099 [P] Write integration test in `tests/002-orchestrator-domain-agents/integration/test_orchestrator_memory.py`: complete a combined plan; assert `orchestrator_memory` row with `memory_type="routing_pattern"`; simulate second plan request; assert rendered system prompt contains routing_memory block; verify GREATEST semantics: same key with lower confidence doesn't overwrite stored value

**Checkpoint**: Orchestrator meta-learning active — routing patterns from successful plans improve future plan generation for the same tenant

---

## Phase 14: User Story 6 — Voice Input (Priority: P2)

**Goal**: `POST /voice` audio upload → `faster-whisper` transcription → same pipeline as text; WER < 15% on Vietnamese.

**Independent Test**: Submit 10 Vietnamese audio clips via `POST /voice`; verify ≥ 9/10 transcriptions < 15% WER; verify response identical quality to equivalent text `POST /chat`; verify 60s+ audio rejected with 422.

- [ ] T100 Implement `orchestrator/stt.py`: `class WhisperSTT` — `__init__(model_size="large-v3", compute_type="int8", download_root="/models")` loads `faster_whisper.WhisperModel` (called once in lifespan); `async def transcribe(audio_bytes: bytes) -> str` — preprocess to 16kHz mono WAV via `asyncio.run_in_executor(None, _run_ffmpeg, audio_bytes)`, then `asyncio.run_in_executor(None, _run_whisper, wav_bytes)` with `language="vi"`, `vad_filter=True`; join segments with space; strip whitespace
- [ ] T101 Add `POST /voice` endpoint to `orchestrator/main.py`: multipart `audio: UploadFile` + optional `session_id: str`; read bytes, validate duration ≤ 60s via ffprobe (reject 422 "audio too long" if exceeded); call `WhisperSTT.transcribe()` → pass to `invoke_chat()`; return `{reply, session_id, intent, transcription: str}` (same schema as `/chat` plus `transcription` field)
- [ ] T102 [P] Add `faster-whisper>=1.0.0` + `ffmpeg-python` to `orchestrator/requirements.txt`; add model volume mount to `docker-compose.yml` service `orchestrator`: `volumes: ["whisper_models:/models"]` + named volume definition to persist large-v3 download
- [ ] T103 [P] Write unit tests in `tests/002-orchestrator-domain-agents/unit/test_stt.py`: test transcription pipeline with 2s audio fixture (mock faster-whisper to avoid CI download); test 422 rejection for > 60s audio; test ffmpeg preprocessing in executor; test empty transcription fallback message

**Checkpoint**: Voice input live — staff can upload audio clips and receive same quality response as text; large-v3 model persisted across container restarts

---

## Phase 15: Polish & Cross-Cutting Concerns

**Purpose**: Observability, eval framework, error hardening, security audit, full quickstart validation

- [ ] T104 Implement `evals/runner.py`: load YAML eval suite file (cases: `[{input, expected_intent, expected_output_contains}]`), call `POST /chat` for each case with Bearer token from env, compare actual vs expected, report pass rate; targets per spec.md SC-001–SC-012
- [ ] T105 [P] Create eval fixture files: `evals/cases/intent_classification.yaml` (20 cases), `evals/cases/entity_extraction.yaml` (20 cases), `evals/cases/nl2sql.yaml` (15 cases), `evals/cases/e2e_order.yaml` (20 cases) — matching acceptance scenarios from spec.md
- [ ] T106 [P] Add structured JSON logging middleware to all 4 services: emit event on every node entry/exit (LangGraph node wrapper decorator), every A2A submit/poll, every Tool Registry call — fields: `agent_id`, `agent_role`, `trace_id`, `tenant_id`, `action`, `status`, `duration_ms` (Constitution IV)
- [ ] T107 [P] Implement `X-Trace-Id` propagation: generate UUID v4 in Orchestrator `POST /chat` if header absent; forward in all `A2ATaskPayload`, `httpx` Tool Registry requests, and LangGraph `config["configurable"]["trace_id"]`; log `trace_id` in every structured event
- [ ] T108 [P] Security audit: grep all service code for any `logging.` / `print()` / `str(token)` / f-string with Bearer token content; verify `a2a:task:{task_id}` Redis hash never contains Authorization header; run `ruff check .` and fix all violations; verify `DATABASE_URL` not logged at startup
- [ ] T109 Implement `AgentRegistry` circuit breaker in `orchestrator/core/agent_registry.py`: mark agent unhealthy in `_healthy` after 3 consecutive `_refresh()` failures; unhealthy agents excluded from `build_prompt_context()` and `DispatchEngine.execute()` ready list; recovery on next successful refresh cycle
- [ ] T110 Validate full quickstart.md end-to-end: run all 9 quickstart sections against live Docker Compose; verify each expected output matches; run full eval suite (`make eval-all`); verify SC-001 ≥ 90% intent accuracy and SC-005 ≥ 85% E2E order success rate

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup)           → no dependencies — start immediately
Phase 2 (Foundational)    → requires Phase 1 — BLOCKS all user story phases
Phase 3 (US5 A2A)         → requires Phase 2 (can parallel with Phase 4)
Phase 4 (US1 Intent)      → requires Phase 2 (can parallel with Phase 3)
Phase 5 (US2 Order)       → requires Phase 3 + Phase 4
Phase 6 (US3 BI)          → requires Phase 3 + Phase 4 (parallel with Phase 5)
Phase 7 (US10 HITL)       → requires Phase 5 (partial — interrupt_before wiring in T046)
Phase 8 (US11 Dispatch)   → requires Phase 5 + Phase 6
Phase 9 (US12 Customer)   → requires Phase 3 + Phase 4 (parallel with Phase 5/6)
Phase 10 (US4 Multi-Turn) → requires Phase 4 (LangGraph wiring already done — explicit tests only)
Phase 11 (US7 Plan)       → requires Phase 8
Phase 12 (US8 AgentMem)   → requires Phase 5 + Phase 6 + Phase 9
Phase 13 (US9 OrchMem)    → requires Phase 11
Phase 14 (US6 Voice)      → requires Phase 4
Phase 15 (Polish)         → requires all previous phases
```

### Story Parallelization Matrix

| Story | Min Dependencies | Can Run Parallel With |
|-------|-----------------|----------------------|
| US5 A2A | Foundational | US1 |
| US1 Intent | Foundational | US5 |
| US2 Order | US5 + US1 | US3, US12 |
| US3 BI | US5 + US1 | US2, US12 |
| US10 HITL | US2 | — |
| US11 Dispatch | US2 + US3 | — |
| US12 Customer | US5 + US1 | US2, US3 |
| US4 Multi-Turn | US1 | US2, US3, US12 |
| US7 Plan | US11 | US8, US9 setup |
| US8 AgentMem | US2+US3+US12 | US9 |
| US9 OrchMem | US7 | US8 |
| US6 Voice | US1 | US8, US9 |

---

## Parallel Examples

### Phase 2: Foundational (maximum parallelism)
```
T006  shared/auth_context.py
T007  shared/llm_client.py          ← parallel with T006
T008  shared/a2a_models.py          ← parallel with T006-T007
T009  shared/tool_registry_client.py ← parallel
T010  shared/memory_service.py       ← parallel
T012  shared/db.py                   ← parallel
T013  shared/redis_client.py         ← parallel
T015  contract/test_a2a_models.py    ← parallel
T016  contract/test_agent_card.py    ← parallel
# Sequential only: T011 (migrations depend on schema design), T014 (tools.yaml)
```

### Phase 5+6+9: Three Domain Agents in parallel
```
Thread A (Order Agent):    T036→T037→T038→T039+T040+T041→T042→T043+T044→T045→T047→T048+T049+T050
Thread B (BI Agent):       T051→T052+T053+T054+T055+T056→T057→T058+T059
Thread C (Customer Agent): T073→T074+T075→T076+T077+T078→T079→T080→T081
```

---

## Implementation Strategy

### MVP (P1 only — Phases 1–9, ~10 working days)

```
Day 1:    Phase 1 (Setup) + Phase 2 start (Foundational shared libs)
Day 2:    Phase 2 complete (migrations, tools.yaml, contract tests)
Day 3-4:  Phase 3 (US5 A2A) + Phase 4 (US1 Intent) — parallel
Day 5-7:  Phase 5+6+9 (Order+BI+Customer agents) — 3 parallel threads
Day 8:    Phase 7 (US10 HITL) — builds on Order Agent interrupt wiring
Day 9:    Phase 8 (US11 Parallel Dispatch) — DispatchEngine + A2AClient
Day 10:   Integration testing + eval baseline run
```

**STOP & VALIDATE**: Run eval suite targets, demo 6 live scenarios per quickstart.md section 3–7

### P2 Incremental (after MVP)

Each P2 story is independently addable:
- US4 (Multi-Turn): just tests — LangGraph already handles this
- US6 (Voice): new STT endpoint only, no existing code changes
- US7 (Plan Visibility): PostgreSQL plan persistence + 2 endpoints
- US8 (Domain Agent Memory): learning extraction + memory injection
- US9 (Orchestrator Memory): routing pattern accumulation

---

## Task Count Summary

| Phase | User Story | Tasks | Priority |
|-------|-----------|-------|---------|
| Phase 1 | Setup | 5 | — |
| Phase 2 | Foundational | 11 | — |
| Phase 3 | US5 A2A Delegation | 7 | P1 |
| Phase 4 | US1 Intent Routing | 12 | P1 |
| Phase 5 | US2 Order Creation | 15 | P1 |
| Phase 6 | US3 BI Queries | 9 | P1 |
| Phase 7 | US10 HITL | 7 | P1 |
| Phase 8 | US11 Parallel Dispatch | 6 | P1 |
| Phase 9 | US12 Customer Management | 9 | P1 |
| Phase 10 | US4 Multi-Turn | 4 | P2 |
| Phase 11 | US7 Plan Visibility | 5 | P2 |
| Phase 12 | US8 Domain Agent Memory | 5 | P2 |
| Phase 13 | US9 Orchestrator Memory | 4 | P2 |
| Phase 14 | US6 Voice Input | 4 | P2 |
| Phase 15 | Polish | 7 | — |
| **Total** | | **119** | |

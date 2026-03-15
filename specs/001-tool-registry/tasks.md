# Tasks: Tool Registry v1

**Input**: Design documents from `specs/001-tool-registry/`
**Prerequisites**: plan.md ✅ spec.md ✅ research.md ✅ data-model.md ✅ contracts/ ✅ quickstart.md ✅

**Tests**: Included — Principle III (Test-First) of the project constitution is NON-NEGOTIABLE.
Contract tests MUST be written and confirmed failing before implementation begins.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no blocking dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths included in every task description

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization — directories, dependencies, Docker Compose, config skeleton

- [x] T001 Create project directory structure: `tool_registry/`, `tool_registry/routers/`, `tool_registry/handlers/`, `shared/`, `config/`, `tests/001-tool-registry/contract/`, `tests/001-tool-registry/integration/`, `tests/001-tool-registry/unit/`
- [x] T002 Create `pyproject.toml` with Python 3.12 dependencies: fastapi>=0.111, pydantic>=2, uvicorn[standard], httpx, watchdog, pyyaml, asyncpg, pytest, pytest-asyncio, respx, ruff
- [x] T003 [P] Add `tool-registry` service to `docker-compose.yml`: image build, port 8001, env vars (`KIOTVIET_API_BASE`, `DATABASE_URL`, `TOOLS_YAML_PATH`), healthcheck `GET /health`
- [x] T004 [P] Create `.env.example` with all required variables: `KIOTVIET_API_BASE`, `DATABASE_URL`, `TOOL_REGISTRY_PORT=8001`, `TOOLS_YAML_PATH=config/tools.yaml`, `HOTRELOAD_INTERVAL_S=1`
- [x] T005 [P] Configure ruff in `pyproject.toml`: `target-version = "py312"`, `line-length = 100`, `select = ["E","F","I","UP"]`
- [x] T006 Create `config/tools.yaml` with all 3 MVP tool definitions from `Agentic_WorkPlan_v2.md` §2.1: `customer__get_customers`, `customer__create_customer`, `order__create_order` (HTTP dispatch), `bi__run_query` (handler dispatch)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core models, auth ContextVar, app factory — MUST complete before any user story

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T007 [P] Create `tests/001-tool-registry/unit/test_auth_context.py` — unit tests: `set_auth`/`get_token`/`get_tenant_id` roundtrip, ContextVar isolation across two concurrent async tasks, empty-string defaults when header absent. Confirm tests FAIL (no implementation yet).
- [x] T008 [P] Create `tests/001-tool-registry/unit/test_models.py` — unit tests: `ToolDefinition` rejects entry with both `api` and `handler` set, rejects entry with neither, `ApiBlock` env-var URL resolves at validation time, `ExecutionResponse` error branch required fields. Confirm tests FAIL.
- [x] T009 Create `shared/auth_context.py`: `ContextVar[str]` for `_token` and `_tenant_id`, `set_auth(token, tenant_id)`, `get_token() -> str`, `get_tenant_id() -> str`, `AuthForwardMiddleware(BaseHTTPMiddleware)` that reads `Authorization` + `X-Tenant-Id` headers and calls `set_auth`. Token MUST NOT be logged.
- [x] T010 [P] Create `tool_registry/models.py` with Pydantic v2 models: `ApiBlock` (method, url, params, body_mapping, item_mapping, response_path, response_rename, response_fields), `HandlerRef` (name), `ToolDefinition` (name, namespace, description, parameters, discriminated `dispatch: ApiBlock | HandlerRef`), `ExecutionRequest` (params), `ExecutionResponse` (success + error variants), `HealthResponse`
- [x] T011 [P] Create `tool_registry/handlers/__init__.py`: `HANDLER_REGISTRY: dict[str, Callable]` empty dict, `register_handler(name: str, fn: Callable)` function
- [x] T012 Create `tool_registry/main.py`: FastAPI `app` factory with `lifespan` async context manager (startup: load YAML config + start watchdog; shutdown: stop watchdog), mount `AuthForwardMiddleware` from `shared/auth_context.py`, mount JSON structured logging middleware (logs `agent_id`, `trace_id`, `action`, `status`, `duration_ms` per request), include `tools` and `health` routers

**Checkpoint**: `python -m pytest tests/001-tool-registry/unit/test_auth_context.py tests/001-tool-registry/unit/test_models.py` — tests must still FAIL (implementations pending in subsequent phases). Foundation structure ready.

---

## Phase 3: User Story 1 — Register Tool via YAML Config (Priority: P1) 🎯 MVP start

**Goal**: Tools defined in `config/tools.yaml` are loadable, validated, hot-reloadable, and accessible in-memory

**Independent Test**: Add a tool to `config/tools.yaml`, start the service, call `GET /tools` (Phase 4), confirm the new tool appears. Modify the file while running, confirm it reloads within 5 s.

### Tests (write first — must FAIL before implementation)

- [x] T013 [P] [US1] Create `tests/001-tool-registry/contract/test_config_loader_contract.py` — contract test: load `config/tools.yaml`, assert each entry parses to a valid `ToolDefinition`, assert `name` matches `namespace__verb` pattern, assert exactly one of `api`/`handler` present. Confirm FAIL.
- [x] T014 [P] [US1] Create `tests/001-tool-registry/unit/test_config_loader.py` — unit tests: duplicate name → second entry skipped + log error emitted, missing `api`/`handler` → entry skipped, `ToolStore.list(namespace="customer")` returns only customer tools, `ToolStore.get("unknown")` returns `None`, `ToolStore.swap()` is atomic. Confirm FAIL.

### Implementation

- [x] T015 [US1] Implement `tool_registry/config_loader.py`: `parse_yaml(path: str) -> list[dict]` (PyYAML load), `validate_tools(raw: list) -> tuple[list[ToolDefinition], list[str]]` (Pydantic validation per entry, collect errors), `build_store(tools: list[ToolDefinition]) -> ToolStore` (populate name dict + namespace index)
- [x] T016 [US1] Implement `ToolStore` class in `tool_registry/config_loader.py`: `get(name) -> ToolDefinition | None`, `list(namespace=None) -> list[ToolDefinition]`, `swap(new: ToolStore)` using `threading.Lock` for atomic replacement, `loaded_at: datetime` field
- [x] T017 [US1] Implement hot-reload in `tool_registry/config_loader.py`: `ToolConfigWatcher(FileSystemEventHandler)` using `watchdog`, `on_modified`: parse staging → validate → if valid swap active store, if invalid log error + retain last good store. Wire into `main.py` lifespan startup.
- [x] T018 [US1] Create `tests/001-tool-registry/integration/test_hotreload.py` — integration test: start watcher on temp `tools.yaml`, write new tool definition, assert `ToolStore` updated within 5 s. Write invalid YAML, assert store unchanged and last good state retained.

**Checkpoint**: `pytest tests/001-tool-registry/contract/test_config_loader_contract.py tests/001-tool-registry/unit/test_config_loader.py tests/001-tool-registry/integration/test_hotreload.py` — all PASS. Config loading is independently functional.

---

## Phase 4: User Story 2 — Discover Tools at Runtime (Priority: P1)

**Goal**: `GET /tools` returns all registered tools in OpenAI function-call format; supports `?namespace=` filter

**Independent Test**: Register two tools in different namespaces. `GET /tools?namespace=customer` returns only customer tools. `GET /tools` returns all.

### Tests (write first — must FAIL before implementation)

- [x] T019 [P] [US2] Create `tests/001-tool-registry/contract/test_get_tools_contract.py` — contract test: `GET /tools` response matches `specs/001-tool-registry/contracts/get-tools.json` schema; each item has `type="function"`, `function.name`, `function.description`, `function.parameters.type="object"`. Confirm FAIL.
- [x] T020 [P] [US2] Create `tests/001-tool-registry/unit/test_tools_router.py` — unit tests: `?namespace=customer` returns only customer tools, `?namespace=unknown` returns empty list `[]`, no filter returns all tools, response is valid JSON array. Confirm FAIL.

### Implementation

- [x] T021 [US2] Implement `to_openai_schema(tool: ToolDefinition) -> dict` function in `tool_registry/routers/tools.py`: wraps `{type: "function", function: {name, description, parameters}}` from `ToolDefinition` fields
- [x] T022 [US2] Implement `GET /tools` endpoint in `tool_registry/routers/tools.py`: call `active_store.list(namespace=query_param)`, map each result through `to_openai_schema()`, return JSON array. Register router in `main.py`.

**Checkpoint**: `pytest tests/001-tool-registry/contract/test_get_tools_contract.py tests/001-tool-registry/unit/test_tools_router.py` — all PASS. Discovery is independently functional.

---

## Phase 5: User Story 3 — Execute a Tool (Priority: P1)

**Goal**: `POST /tools/{name}/execute` routes to HTTP adapter or Python handler, forwards Bearer token, returns normalized result

**Independent Test**: `POST /tools/customer__get_customers/execute` with Bearer token → registry forwards to mock backend with correct Authorization header and returns normalized response. `POST /tools/bi__run_query/execute` → invokes `bi_query_handler` directly.

### Tests (write first — must FAIL before implementation)

- [x] T023 [P] [US3] Create `tests/001-tool-registry/contract/test_execute_contract.py` — contract test: `POST /tools/customer__get_customers/execute` response matches `specs/001-tool-registry/contracts/execute-tool.json` 200 schema; 404 response matches TOOL_NOT_FOUND schema; 504 response matches TIMEOUT schema. Confirm FAIL.
- [x] T024 [P] [US3] Create `tests/001-tool-registry/unit/test_dispatch.py` — unit tests: `ApiBlock` tool routes to HTTP path, `HandlerRef` tool routes to handler path, timeout after 10 s raises `TIMEOUT` error, 2 retries attempted on network error, token from ContextVar forwarded in `Authorization` header, token NOT present in any logged output. Confirm FAIL.

### Implementation

- [x] T025 [US3] Implement HTTP dispatch path in `tool_registry/dispatch.py`: `execute_http(tool: ToolDefinition, params: dict, auth: AuthContext) -> dict` — build URL (env-var resolved at load), build `params` or `body` from `api.params`/`api.body_mapping`, call via `httpx.AsyncClient` with `timeout=10.0`, retry up to 2 times on `httpx.TransportError`, apply `response_path` + `response_rename` + `response_fields` normalization
- [x] T026 [US3] Implement handler dispatch path in `tool_registry/dispatch.py`: `execute_handler(tool: ToolDefinition, params: dict, auth: AuthContext) -> dict` — resolve `HandlerRef.name` from `HANDLER_REGISTRY`, call `await handler(params, auth)`, raise `ToolExecutionError(HANDLER_ERROR)` on exception
- [x] T027 [US3] Implement `dispatch(tool: ToolDefinition, params: dict) -> dict` in `tool_registry/dispatch.py`: read auth from `get_token()`/`get_tenant_id()` ContextVars, branch on `isinstance(tool.dispatch, ApiBlock)` vs `HandlerRef`, map exceptions to typed `ExecutionResponse` errors (TOOL_NOT_FOUND, TIMEOUT, BACKEND_ERROR, VALIDATION_ERROR, HANDLER_ERROR) in English
- [x] T028 [US3] Implement `bi_query_handler` in `tool_registry/handlers/bi_query_handler.py`: register into `HANDLER_REGISTRY` at import, accept `params: dict` with `sql` and `limit`, validate SELECT-only (reject if `sql.strip().upper()` does not start with `SELECT`), inject `LIMIT {limit}` if absent, execute via `asyncpg` connection pool using `DATABASE_URL`, return rows as `list[dict]`
- [x] T029 [US3] Implement `POST /tools/{name}/execute` endpoint in `tool_registry/routers/tools.py`: look up tool by name (404 if missing), validate `params` against tool's JSON Schema, call `dispatch(tool, params)`, return `ExecutionResponse`. Emit structured log: `tool_name`, `trace_id`, `status`, `duration_ms` (token masked).
- [x] T030 [P] [US3] Create `tests/001-tool-registry/integration/test_http_dispatch.py` — integration test using `respx` to mock backend: customer tool execution returns mocked response, `response_rename` applied correctly, Authorization header forwarded to mock, timeout mock triggers TIMEOUT error code
- [x] T031 [P] [US3] Create `tests/001-tool-registry/integration/test_handler_dispatch.py` — integration test with mock asyncpg: `bi__run_query` executes SELECT query, non-SELECT SQL rejected with VALIDATION_ERROR, LIMIT injected when absent

**Checkpoint**: `pytest tests/001-tool-registry/contract/test_execute_contract.py tests/001-tool-registry/unit/test_dispatch.py tests/001-tool-registry/integration/test_http_dispatch.py tests/001-tool-registry/integration/test_handler_dispatch.py` — all PASS. Execution is independently functional.

---

## Phase 6: User Story 5 — ToolRegistryClient Library (Priority: P1)

**Goal**: `ToolRegistryClient` shared library provides `get_openai_tools()` and `execute()` with transparent token forwarding for all consuming agents

**Independent Test**: Instantiate `ToolRegistryClient(base_url="http://localhost:8001")`, call `get_openai_tools("customer")` → returns OpenAI format list. Call `execute("customer__get_customers", {...})` → registry receives correct Authorization header.

### Tests (write first — must FAIL before implementation)

- [x] T032 [P] [US5] Create `tests/001-tool-registry/unit/test_tool_registry_client.py` — unit tests: `get_openai_tools()` returns list with `type="function"` items, `get_openai_tools(namespace="bi")` passes `?namespace=bi` query param, `execute()` sets `Authorization: Bearer <token>` from ContextVar, `execute()` raises descriptive exception when registry returns 404, `execute()` raises on connection error within timeout. Confirm FAIL.
- [x] T033 [P] [US5] Create `tests/001-tool-registry/integration/test_client.py` — integration test against `httpx.AsyncClient` mock: full roundtrip `get_openai_tools()` → `execute()` with real `ToolRegistryClient` instance; confirm token forwarded end-to-end. Confirm FAIL.

### Implementation

- [x] T034 [US5] Create `shared/tool_registry_client.py`: `ToolRegistryClient(base_url: str)` class with `httpx.AsyncClient` session, `async get_openai_tools(namespace: str | None = None) -> list[dict]` (calls `GET /tools?namespace=...`, returns parsed JSON), `async execute(name: str, params: dict) -> dict` (reads token from `get_token()` ContextVar, calls `POST /tools/{name}/execute` with `Authorization` header, returns `result` field from response, raises `ToolRegistryError` on non-200)
- [x] T035 [US5] Add `ToolRegistryError` exception class to `shared/tool_registry_client.py` with `error_code: str` and `tool_name: str` fields for structured error propagation to agent callers

**Checkpoint**: `pytest tests/001-tool-registry/unit/test_tool_registry_client.py tests/001-tool-registry/integration/test_client.py` — all PASS. Client is independently usable.

---

## Phase 7: User Story 4 — Service Health Check (Priority: P2)

**Goal**: `GET /health` returns service status and loaded tool count; Docker Compose can gate on it

**Independent Test**: Start service with valid `tools.yaml` → `GET /health` returns `200 {"status": "healthy", "tool_count": N}`. Start with invalid YAML → `GET /health` returns `503 {"status": "degraded"}`.

### Tests (write first — must FAIL before implementation)

- [x] T036 [P] [US4] Create `tests/001-tool-registry/contract/test_health_contract.py` — contract test: healthy response matches `specs/001-tool-registry/contracts/health.json` 200 schema (status, tool_count, config_path, last_reload fields); degraded response matches 503 schema. Confirm FAIL.

### Implementation

- [x] T037 [US4] Create `tool_registry/routers/health.py`: `GET /health` endpoint — reads `active_store.tool_count`, `active_store.loaded_at`, `active_store.config_path`; returns `HealthResponse(status="healthy", ...)` if store loaded successfully, `HealthResponse(status="degraded", error=...)` with HTTP 503 if startup config load failed. Register router in `main.py`.

**Checkpoint**: `pytest tests/001-tool-registry/contract/test_health_contract.py` — PASS. Health endpoint independently functional and Docker Compose healthcheck can use it.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: End-to-end validation, auth token safety audit, documentation

- [x] T038 [P] Create `tests/001-tool-registry/integration/test_e2e_registry.py` — end-to-end test: load `config/tools.yaml` with all 3 namespaces via test client, `GET /tools` returns all tools in OpenAI format, execute each namespace tool against mocks, `GET /health` reports correct `tool_count=N`
- [x] T039 Add `conftest.py` to `tests/001-tool-registry/` — shared fixtures: `async_client` (FastAPI `AsyncClient` with test app), `temp_tools_yaml` (temp file fixture), `mock_kiotviet_backend` (respx mock for KiotViet API base URL)
- [x] T040 [P] Security audit: grep all log output in tests for any occurrence of the test Bearer token string; assert zero matches (token masking validation per FR-005)
- [x] T041 [P] Validate `quickstart.md` curl examples against running test service — run each `curl` command from `specs/001-tool-registry/quickstart.md` sections 4, 5, 7 against the test client and confirm expected responses
- [x] T042 Update `CLAUDE.md` between `<!-- MANUAL ADDITIONS START -->` and `<!-- MANUAL ADDITIONS END -->` with tool_registry service startup command and test command

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately
- **Foundational (Phase 2)**: Depends on Phase 1 completion — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Phase 2 — no other story dependency
- **US2 (Phase 4)**: Depends on Phase 2 + Phase 3 (needs active `ToolStore`)
- **US3 (Phase 5)**: Depends on Phase 2 + Phase 3 (needs `ToolStore` for dispatch lookup)
- **US5 (Phase 6)**: Depends on Phase 4 + Phase 5 (client wraps both endpoints)
- **US4 (Phase 7)**: Depends on Phase 2 + Phase 3 (reads `active_store`)
- **Polish (Phase 8)**: Depends on all story phases complete

### User Story Dependencies

- **US1 (P1)**: Foundational only
- **US2 (P1)**: US1 must be complete (needs loaded ToolStore)
- **US3 (P1)**: US1 must be complete (needs loaded ToolStore for dispatch)
- **US5 (P1)**: US2 + US3 must be complete (client wraps both endpoints)
- **US4 (P2)**: US1 must be complete (reads ToolStore health)

### Within Each User Story

1. Write tests first → confirm FAIL (Red)
2. Implement → confirm PASS (Green)
3. Refactor if needed → confirm still PASS

### Parallel Opportunities

- T003, T004, T005 (Phase 1) — all parallel, different files
- T007, T008, T010, T011 (Phase 2) — all parallel, different files
- T013, T014 (US1 tests) — parallel
- T019, T020 (US2 tests) — parallel
- T023, T024 (US3 tests) — parallel
- T025, T026 (US3 HTTP + handler dispatch) — parallel, different functions
- T030, T031 (US3 integration tests) — parallel
- T032, T033 (US5 tests) — parallel
- T038, T039, T040, T041 (Polish) — parallel

---

## Parallel Example: User Story 3 (Execute Tool)

```bash
# Step 1 — Write tests in parallel (both fail):
Task T023: "Create tests/001-tool-registry/contract/test_execute_contract.py"
Task T024: "Create tests/001-tool-registry/unit/test_dispatch.py"

# Step 2 — Implement dispatch in parallel (after tests written):
Task T025: "Implement HTTP dispatch in tool_registry/dispatch.py"
Task T026: "Implement handler dispatch in tool_registry/dispatch.py"
Task T028: "Implement bi_query_handler in tool_registry/handlers/bi_query_handler.py"

# Step 3 — Integration tests in parallel:
Task T030: "Create tests/.../test_http_dispatch.py"
Task T031: "Create tests/.../test_handler_dispatch.py"
```

---

## Implementation Strategy

### MVP First (US1 + US2 + US3 — the three P1 core stories)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL)
3. Complete Phase 3: US1 — tools load from YAML
4. Complete Phase 4: US2 — tools discoverable via GET /tools
5. Complete Phase 5: US3 — tools executable via POST /execute
6. **STOP and VALIDATE**: curl examples from quickstart.md work end-to-end
7. Demo: Orchestrator can discover and invoke all 3 namespace tools

### Incremental Delivery

1. Setup + Foundational → structure ready
2. +US1 (YAML loading) → config pipeline proven
3. +US2 (discovery) → agents can list tools ← **Sprint 1 Day 3 checkpoint**
4. +US3 (execution) → agents can invoke tools ← **Sprint 1 Day 3 complete**
5. +US5 (client) → agents use ToolRegistryClient ← **Sprint 1 Day 4 complete**
6. +US4 (health) + Polish → production-ready

### Sprint Alignment (Agentic_WorkPlan_v2.md)

- **Day 3** deliverable: `GET /tools` working + `POST /tools/{name}/execute` for KiotViet tools → Phase 3 + 4 + 5 complete
- **Day 4** deliverable: `ToolRegistryClient` shared lib → Phase 6 complete

---

## Notes

- [P] tasks operate on different files — safe to parallelize
- [Story] label maps every task to a specific user story for traceability
- Token masking is a hard requirement — T040 validates it
- Hot-reload atomicity (T017) is critical for production correctness — write test T018 first
- `bi_query_handler` SELECT-only check (T028) is a security boundary — test must validate rejection
- All error messages must be in English (clarification Q5)
- Commit after each checkpoint — checkpoints align with Sprint 1 daily deliverables

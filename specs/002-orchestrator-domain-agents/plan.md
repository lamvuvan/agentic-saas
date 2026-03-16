# Implementation Plan: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Date**: 2026-03-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-orchestrator-domain-agents/spec.md`

## Summary

Build a three-service multi-agent system (Orchestrator :8000, Order Agent :8002, BI Agent :8003) on top of the existing Tool Registry (:8001). The Orchestrator classifies Vietnamese free-text messages into `order | bi_query | chitchat | unknown`, generates a serializable ExecutionPlan, delegates tasks to the appropriate Domain Agent via the A2A protocol, and aggregates results into a final Vietnamese reply. Voice input is **out of scope** — the endpoint accepts text only; if callers need STT, they handle it client-side before calling `/chat`.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI 0.111+, Pydantic v2, LangGraph 0.2+, LangChain, langchain-openai, langgraph-checkpoint-redis, httpx (async), asyncpg, sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`), faiss-cpu, redis, openai
**Storage**: Redis (LangGraph checkpointer, session state, A2A task store — 30-min session TTL, 1-hr task TTL); PostgreSQL with pgvector (analytics DB, read-only via BI Agent)
**Testing**: pytest, pytest-asyncio, respx (HTTP mocking)
**Target Platform**: Linux server, Docker Compose
**Project Type**: Multi-service web API (3 new FastAPI services)
**Performance Goals**: P95 order conversation ≤ 3s; P95 BI query ≤ 5s
**Constraints**: Bearer token MUST NOT appear in any log, DB record, or response body; SELECT-only SQL; LIMIT 500 auto-injected; max 3 replanning attempts per Orchestrator task
**Scale/Scope**: Single-tenant MVP; ~10 concurrent staff users per deployment

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked post-design.*

| Check | Status | Notes |
|---|---|---|
| **I. Agent-First Design** — each service has single responsibility | ✅ PASS | Orchestrator orchestrates only; Order Agent handles orders only; BI Agent handles analytics only |
| **I. Orchestrator archetype** — reasoning + planning, no domain work | ✅ PASS | classify→plan→dispatch→aggregate; all domain execution delegated via A2A |
| **I. Domain Agent archetype** — bounded domain, no cross-agent calls | ✅ PASS | Order Agent: `domain: order`; BI Agent: `domain: bi`; neither calls the other |
| **II. Tool Registry First** — all tools registered before use | ✅ PASS | `customer__get_customers`, `customer__create_customer`, `order__create_order`, `bi__run_query` already in `config/tools.yaml` |
| **II. A2A Agent Cards** — published at `/.well-known/agent.json` | ✅ PASS | All three services expose Agent Card endpoint; AgentRegistry fetches cards at startup and refreshes every 60s; Plan node injects live manifest into system prompt (Constitution §II: "Orchestrators MUST use Agent Cards for capability discovery") |
| **II. MCP layer** — Domain Agents access tools via Tool Registry, not raw HTTP | ✅ PASS | `ToolRegistryClient` in `shared/` wraps all tool calls |
| **III. Test-First** — contract tests written before implementation | ✅ PASS | `tests/002-orchestrator-domain-agents/contract/` committed first |
| **III. Orchestrator plan generation tested** | ✅ PASS | plan node tested for order + bi cases; failure path covered |
| **IV. Structured logging** — `trace_id`, `agent_role`, `duration_ms`, `status` | ✅ PASS | JSON middleware on all three services |
| **IV. Plan persistence** | ✅ PASS | `plan:{plan_id}` persisted to Redis immediately after generation (Constitution §V) |
| **V. Resilience** — timeouts, idempotent A2A, plan re-entrant | ✅ PASS | A2A timeout 30s; draft keyed by `task_id`; max_replans=3 |
| **VI. Multi-tenancy** | ⚠️ DEFERRED | Single-tenant MVP; `tenant_id` propagated via ContextVar but partition enforcement is v2 backlog |
| **VII. Simplicity** — no premature abstraction | ✅ PASS | Direct async functions used instead of full LangGraph StateGraph where equivalent |

**Complexity Tracking**:

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Multi-tenant isolation deferred (Principle VI) | Single-tenant MVP scope; first deployment is one restaurant chain | Adding full partition enforcement now would require schema design not yet decided for v2 DB-backed Tool Registry |

## Project Structure

### Documentation (this feature)

```text
specs/002-orchestrator-domain-agents/
├── plan.md              # This file
├── research.md          # Technology decisions (FAISS thresholds, LangGraph checkpoint, A2A polling)
├── data-model.md        # All entity schemas (A2ATask, ExecutionPlan, OrderDraft, BIQueryResult, etc.)
├── quickstart.md        # Local dev setup guide
├── contracts/           # API contracts: orchestrator-chat, a2a-task, order-agent-response, bi-agent-response
└── tasks.md             # 73 implementation tasks, 9 phases
```

### Source Code

```text
orchestrator/                   # :8000 — Orchestrator Agent
├── main.py                     # FastAPI app, /chat, /health, /.well-known/agent.json, lifespan
├── graph.py                    # invoke_chat(): classify → plan → dispatch → aggregate
├── agent_registry.py           # AgentRegistry: fetch /.well-known/agent.json from seed URLs, background refresh 60s, build_prompt_context(), get_a2a_endpoint()
├── session.py                  # Redis-backed session: 30-min TTL, last-20-turns, get_last_n(3)
├── models.py                   # OrchestratorState, IntentResult, ChatRequest, ChatResponse
├── a2a_client.py               # submit_to_agent(), poll_for_result() — wraps shared/a2a/client.py
├── nodes/
│   ├── intent_classify.py      # GPT-4o-mini, escalates to GPT-4o at confidence < 0.72
│   ├── plan.py                 # GPT-4o, injects AgentRegistry.build_prompt_context() into prompt, outputs ExecutionPlan, persists plan:{plan_id} to Redis
│   ├── a2a_dispatch.py         # Resolves agent URL via AgentRegistry (fallback: env vars), submits A2A tasks, polls, handles timeout/replan
│   └── aggregate.py            # Synthesises Domain Agent results → Vietnamese reply
└── prompts/
    ├── intent_classify_v1.md   # System prompt + 8 few-shot examples
    └── plan_v1.md              # Planning prompt with {agent_manifest} placeholder + 2 few-shot examples

order_agent/                    # :8002 — Order Domain Agent
├── main.py                     # FastAPI app, A2A router, /.well-known/agent.json, lifespan
├── a2a_server.py               # POST /a2a/tasks (202), GET /a2a/tasks/{id}, continuation resume
├── graph.py                    # run_order_graph(): extract → match → customer → preview → confirm → submit
├── product_matcher.py          # FAISS IndexFlatIP, atomic refresh, threshold routing (0.85/0.65)
├── vn_utils.py                 # normalize_text, strip_honorifics, words_to_numbers, extract_product_note
├── models.py                   # OrderAgentState, OrderEntities, ProductMatch, OrderDraft, ResolvedOrderItem
├── nodes/
│   ├── extract_entities.py     # GPT-4o-mini Structured Output → OrderEntities
│   ├── match_products.py       # FAISS search + LLM rerank for 0.65–0.84 range
│   ├── check_customer.py       # Tool Registry customer__get_customers / create_customer
│   ├── preview.py              # Build OrderDraft + Vietnamese preview message
│   ├── confirm.py              # check_confirmation(): confirmed | cancelled | unknown
│   └── submit_order.py         # Tool Registry order__create_order
└── prompts/
    └── entity_extract_v1.md    # 12 Vietnamese few-shot examples

bi_agent/                       # :8003 — BI Domain Agent
├── main.py                     # FastAPI app, A2A router, /.well-known/agent.json, lifespan
├── a2a_server.py               # POST /a2a/tasks (202), GET /a2a/tasks/{id}
├── graph.py                    # run_bi_graph(): schema_explorer → nl2sql → safety_check → execute → format
├── models.py                   # BIAgentState, BIQueryResult
├── nodes/
│   ├── schema_explorer.py      # Load config/bi_schema.yaml, cache schema context string
│   ├── nl2sql.py               # GPT-4o, temperature=0, {{SCHEMA_CONTEXT}} injection
│   ├── safety_check.py         # SELECT-only + blocklist; inject_limit(sql, 500)
│   ├── execute_query.py        # Tool Registry bi__run_query
│   └── format_response.py      # GPT-4o-mini → Vietnamese summary
└── prompts/
    ├── nl2sql_v1.md             # Schema injection template + 6 Vietnamese few-shot
    └── format_response_v1.md   # Vietnamese formatting guidelines

shared/                         # Cross-service shared libraries
├── a2a/
│   ├── __init__.py             # Exports all A2A models
│   ├── models.py               # A2ATask, A2AResult, AgentCard, ExecutionPlan, PlanStep, TaskStatus
│   ├── client.py               # submit_task(), poll_task(), A2ATimeoutError
│   └── server.py               # store_task(), get_task(), update_task_status() — Redis a2a:task:{id}
├── llm_client.py               # select_model(task_type), chat_completion_async(), ESCALATION_THRESHOLD=0.72
├── auth_context.py             # ContextVar token/tenant_id, AuthForwardMiddleware (from feature 001)
└── tool_registry_client.py     # ToolRegistryClient (from feature 001)

config/
├── tools.yaml                  # Tool Registry definitions (customer, order, bi namespaces)
└── bi_schema.yaml              # Table allowlist + business glossary for NL2SQL

tests/002-orchestrator-domain-agents/
├── conftest.py                 # reset_auth_context fixture (autouse)
├── contract/
│   ├── test_chat_contract.py   # POST /chat schema, 422 validation, token not in response
│   ├── test_a2a_contract.py    # POST /a2a/tasks 202, GET status enum, 404, token not echoed
│   ├── test_order_agent_contract.py  # Output schema: output+reasoning_summary, input_request
│   └── test_bi_agent_contract.py     # BI output schema, rejected structure, token not in result
├── integration/
│   ├── test_token_forwarding.py      # Authorization header propagation, token never in task record
│   ├── test_a2a_lifecycle.py         # submitted→terminal, UUID v4, timestamps
│   ├── test_intent_routing.py        # order/bi/chitchat routing, session_id preservation
│   ├── test_order_flow.py            # Single/multi-item order, continuation resume
│   └── test_bi_query.py             # Revenue/customer queries, destructive query rejected
└── unit/
    ├── test_vn_utils.py             # 50 cases: normalize, strip_honorifics, words_to_numbers
    ├── test_product_matcher.py      # Index build, threshold routing, atomic refresh
    └── test_nl2sql.py               # Safety check 12 cases, inject_limit 5 cases

evals/
├── runner.py                   # CLI: python -m evals.runner --suite X --base-url Y
└── cases/
    ├── intent_classification.yaml   # 20 cases (order/bi/chitchat/edge)
    ├── entity_extraction.yaml       # 20 cases (numbers, honorifics, multi-item, table, notes)
    ├── nl2sql.yaml                  # 20 cases (revenue, customer, debt, inventory, time range)
    └── e2e_order.yaml               # 20 end-to-end conversation scenarios
```

**Structure Decision**: Multi-service monorepo. Each service is a top-level directory with its own `main.py` and `Dockerfile`. Shared libraries live in `shared/`. This mirrors the Tool Registry pattern from feature 001 and keeps service boundaries explicit without requiring a separate repo per service.

## Key Design Decisions

### Agent Discovery (AgentRegistry)

Each Domain Agent already publishes `/.well-known/agent.json`. Rather than hardcoding agent URLs and skills in the planning prompt, the Orchestrator runs an `AgentRegistry` that:

1. Reads `AGENT_SEED_URLS` (comma-separated base URLs, e.g. `http://order-agent:8002,http://bi-agent:8003`)
2. Fetches each Agent Card at startup; spawns a background task to re-fetch every 60s
3. Tracks health per agent — failed fetch → marked unhealthy
4. `build_prompt_context()` renders only healthy agents and their skills as markdown, injected into the plan prompt via `{agent_manifest}` placeholder in `plan_v1.md`
5. `get_a2a_endpoint(agent_name)` is used by `a2a_dispatch.py`; falls back to `ORDER_AGENT_URL`/`BI_AGENT_URL` env vars if registry unavailable

**Adding a new agent**: add its base URL to `AGENT_SEED_URLS` and restart the Orchestrator — the new agent is discovered within the next refresh cycle (≤ 60s from next startup).

**Files**: `orchestrator/agent_registry.py` (new), `orchestrator/main.py` (lifespan), `orchestrator/nodes/plan.py` (registry param), `orchestrator/nodes/a2a_dispatch.py` (_resolve_agent_url), `orchestrator/prompts/plan_v1.md` ({agent_manifest})

### Voice: Out of Scope
Voice input (US6, FR-007) is **removed from this plan**. The `/chat` endpoint accepts text only. If callers need voice-to-text, they perform STT client-side before calling the API. This eliminates the `faster-whisper` dependency, the `/voice` endpoint, and the `orchestrator/stt.py` module.

Removed from spec scope:
- US6 — Voice Input
- FR-007 — voice endpoint
- SC-009 — WER metric
- SC-012 amended: 5 live demo scenarios (2 order chat, 1 BI revenue, 1 BI customer ranking, 1 BI debt); no voice scenario

### Model Routing
| Task type | Model | Threshold |
|---|---|---|
| intent_classify, entity_extract, product_rerank, response_format | GPT-4o-mini | default |
| orchestrator_plan, nl2sql | GPT-4o | default |
| intent_classify (low confidence) | GPT-4o | escalate when confidence < 0.72 |

### A2A Task Store
Redis hash `a2a:task:{task_id}`. TTL applied only on terminal states (COMPLETED, FAILED, TIMEOUT): 3600s. Non-terminal states have no TTL to allow long-running confirmation flows.

### LangGraph Checkpointing
- Orchestrator: `thread_id = session_id` — preserves conversation state across `/chat` calls
- Order Agent: `thread_id = task_id` — enables confirmation resume via `ainvoke(None, config)` with same thread_id

### FAISS Thresholds
- ≥ 0.85 → auto-select (IndexFlatIP cosine similarity, L2-normalized vectors)
- 0.65–0.84 → GPT-4o-mini rerank from top-3 candidates
- < 0.65 → ask user to re-describe

### Session State
Redis key `session:{session_id}`, TTL 1800s (30 min). Stores last 20 turns; injects last 3 into LLM context. `active_task_id` field tracks in-progress A2A task for continuation routing.

## Implementation Phases

See [tasks.md](tasks.md) for the full 73-task breakdown across 9 phases:

| Phase | Scope | Key deliverable |
|---|---|---|
| 1 — Setup | pyproject.toml, Docker Compose, `__init__.py` | All services start healthy |
| 2 — Foundational | shared/a2a/, shared/llm_client.py, A2A contract tests | Shared libs + contract tests pass |
| 3 — A2A Protocol (US5) | A2A server/client, task lifecycle, token forwarding | A2A E2E: submitted→working→completed |
| 4 — Intent Routing (US1) | Orchestrator graph, intent_classify, plan, dispatch, aggregate; **AgentRegistry + dynamic plan prompt** | classify 20 inputs ≥ 90%; new agent appears in prompt ≤ 60s |
| 5 — Order Creation (US2) | Order Agent full pipeline, FAISS, VN utils, multi-turn confirm | E2E order ≥ 85% on 20 inputs |
| 6 — BI Queries (US3) | BI Agent: NL2SQL, safety, execute, format | BI correctness ≥ 80% on 20 queries |
| 7 — Multi-Turn State (US4) | session.py, LangGraph checkpointer, continuation resume | 3-turn order conversation preserves state |
| 8 — ~~Voice~~ (removed) | — | — |
| 9 — Polish | Eval runner + 4 YAML suites, structured logging, CLAUDE.md update | CI eval pipeline; 60+ golden test cases |

## Success Criteria (Voice Removed)

| # | Criterion | Target | Measured by |
|---|---|---|---|
| SC-001 | Intent classification accuracy | ≥ 90% | 20-case eval suite |
| SC-002 | Entity extraction precision + recall | ≥ 90% | 20 YAML test cases |
| SC-003 | Product matching recall@1 | ≥ 85% | 30 product name variations |
| SC-004 | NL2SQL correctness | ≥ 80% | 20 representative queries |
| SC-005 | E2E order creation success | ≥ 85% | 20 full-conversation test cases |
| SC-006 | E2E BI query success | ≥ 80% | 20 BI query test cases |
| SC-007 | P95 order chat latency | ≤ 3s | 30 requests, concurrent=1 |
| SC-008 | P95 BI query latency | ≤ 5s | 20 queries, concurrent=1 |
| SC-010 | Zero Bearer token leaks | 100% | Code review + log audit |
| SC-011 | A2A lifecycle correctness | 100% | Automated test suite |
| SC-012 | Live demo (5 scenarios, 0 unhandled errors) | 0 crash | Demo video |

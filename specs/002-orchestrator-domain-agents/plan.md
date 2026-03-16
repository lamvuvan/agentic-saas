# Implementation Plan: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Date**: 2026-03-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-orchestrator-domain-agents/spec.md`

## Summary

Implement three cooperating agents — Orchestrator, Order Domain Agent, and BI Domain Agent — that collectively let retail/restaurant staff create orders and query business data using Vietnamese natural language. The Orchestrator classifies intent, creates a serializable execution plan, and delegates to Domain Agents via the A2A protocol. Domain Agents use LangGraph reasoning loops, the Tool Registry for tool access, and return structured results including a `reasoning_summary`. Voice input is supported via STT. The system builds on the already-deployed Tool Registry (feature 001).

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI 0.111+, LangGraph 0.2+, LangChain, langchain-openai, langgraph-checkpoint-redis, httpx, sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu, faster-whisper, asyncpg, pydantic v2, pytest, pytest-asyncio, respx, ruff
**LLM Models**: GPT-4o-mini (intent classification, entity extraction, response formatting); GPT-4o (orchestrator planning, NL2SQL); o1-mini (fallback for complex cross-domain planning — out of scope for v1 MVP)
**Storage**: Redis (LangGraph checkpointer + session state + A2A task store); PostgreSQL (analytics DB for BI queries — already deployed); No new DB schema for v1
**Testing**: pytest + pytest-asyncio + respx (HTTP mocking) + LangGraph test utilities
**Target Platform**: Linux container (Docker Compose), ports 8000/8002/8003
**Performance Goals**: P95 order conversation ≤ 3s; P95 BI query ≤ 5s; Voice WER < 15%
**Constraints**: Bearer token forwarded via ContextVar, never stored; all A2A calls include auth header; replanning budget ≤ 3 attempts per request; A2A timeout = 30s
**Scale/Scope**: Single-tenant MVP; 3 services; ~10 concurrent users; ~100 req/day initially

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|-----------|--------|----------|
| **I. Agent-First Design** | ✅ PASS | Orchestrator (role: orchestrator) + Order Agent (role: domain, domain: order) + BI Agent (role: domain, domain: bi). Each has a single responsibility. Orchestrator never executes domain tools directly. |
| **II. Contract-Driven Communication** | ✅ PASS | All tools via Tool Registry (001). A2A protocol for agent-to-agent. Agent Cards at `/.well-known/agent.json`. Contracts defined before implementation. |
| **III. Test-First (NON-NEGOTIABLE)** | ✅ PASS | Contract tests written before implementation. A2A task lifecycle tested. Orchestrator plan generation and replanning have dedicated test scenarios. |
| **IV. Observability** | ✅ PASS | Structured JSON logging: `agent_id`, `agent_role`, `trace_id`, `tenant_id`, `action`, `status`, `duration_ms`. Reasoning traces linked to `trace_id`. Orchestrator plans persisted to Redis. |
| **V. Resilience** | ✅ PASS | LangGraph plans serialized to Redis (re-entrant). Timeouts on all A2A calls (30s). Replanning budget ≤ 3. Tool Registry health-aware routing. |
| **VI. Multi-Tenancy** | ⚠️ PARTIAL | v1 is single-tenant MVP. Session state keyed by `session_id` without tenant prefix. Tenant isolation deferred to v1.1. |
| **VII. Simplicity** | ✅ PASS | HTTP polling A2A (not SSE streaming) — justified by MVP scope. FAISS in-memory (no vector DB service) — justified by dataset size. |

**Agent Role Taxonomy Check** (Constitution §Governance, required for agent-introducing features):

| Gate | Status | Detail |
|------|--------|--------|
| Agent Card declares `role` | ✅ | Orchestrator: `role: orchestrator`; Order/BI: `role: domain` |
| Tool Registry registration in foundational tasks | ✅ | Order Agent and BI Agent tools registered as Phase 2 tasks |
| A2A Agent Card publication in foundational tasks | ✅ | Agent Cards at `/.well-known/agent.json` — Phase 2 task |
| MCP server definition | ✅ | Tool Registry (001) serves as MCP layer; Order/BI Agents consume via `ToolRegistryClient` |
| Orchestrator: plan schema defined | ✅ | `ExecutionPlan` schema in `shared/a2a/models.py` |
| Orchestrator: plan store provisioned | ✅ | Redis checkpointer via `langgraph-checkpoint-redis` |
| Orchestrator: replanning budget set | ✅ | `MAX_REPLAN_ATTEMPTS = 3` in Orchestrator graph config |
| Domain Agents: `reasoning_summary` in output contract | ✅ | Defined in A2A response contracts |
| Domain Agents: domain scope declared in Agent Card | ✅ | Order: `domain: order`; BI: `domain: bi` |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Principle VI partial: no per-tenant session isolation | Single-tenant MVP; faster to ship without per-tenant partitioning | Full multi-tenancy adds 3-4 days; not required for internal demo |
| HTTP polling A2A instead of SSE streaming | MVP scope; polling over 30s window with 1s intervals is sufficient | SSE adds significant streaming infrastructure; latency targets (3s/5s) are met with polling |
| FAISS in-memory index instead of dedicated vector DB | Product catalog ≤ 10k items; FAISS in-memory is ≤ 100ms for this size | pgvector/Weaviate adds service dependency; overkill for ≤ 10k products |

## Project Structure

### Documentation (this feature)

```text
specs/002-orchestrator-domain-agents/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── chat.json                   # POST /chat contract
│   ├── voice.json                  # POST /voice contract
│   ├── a2a-task.json               # A2A task protocol (shared)
│   ├── order-agent-response.json   # Order Agent A2A result
│   ├── bi-agent-response.json      # BI Agent A2A result
│   └── agent-card.json             # Agent Card schema
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created here)
```

### Source Code (repository root)

```text
orchestrator/
├── main.py                        # FastAPI app factory, port 8000, lifespan
├── graph.py                       # LangGraph StateGraph: classify→plan→dispatch→aggregate
├── nodes/
│   ├── intent_classify.py         # GPT-4o-mini, structured JSON output, 8 few-shot
│   ├── plan.py                    # GPT-4o, ExecutionPlan schema, CoT reasoning
│   ├── a2a_dispatch.py            # A2A client calls to Domain Agents
│   └── aggregate.py               # Response synthesis from agent results
├── a2a_client.py                  # POST /a2a/tasks + poll GET /a2a/tasks/{id}
├── models.py                      # OrchestratorState, ExecutionPlan, IntentResult
├── session.py                     # Session history management (Redis, last 3 turns)
├── stt.py                         # faster-whisper, POST /voice handler
└── prompts/
    ├── intent_classify_v1.md      # Versioned intent classification prompt
    └── plan_v1.md                 # Versioned planning prompt

order_agent/
├── main.py                        # FastAPI app factory, port 8002, lifespan
├── graph.py                       # LangGraph: extract→match→customer→preview→confirm→submit
├── nodes/
│   ├── extract_entities.py        # GPT-4o-mini, OrderEntities structured output
│   ├── match_products.py          # FAISS cosine search + GPT-4o-mini rerank
│   ├── check_customer.py          # Tool Registry: customer__get_customers
│   ├── preview.py                 # Build order draft, format preview message
│   ├── confirm.py                 # LangGraph interrupt — wait for user confirmation
│   └── submit_order.py            # Tool Registry: order__create_order
├── a2a_server.py                  # POST /a2a/tasks, GET /a2a/tasks/{id}, task store
├── product_matcher.py             # ProductMatcher: FAISS index, build/search/refresh
├── vn_utils.py                    # Vietnamese NLP utils: num words, honorific strip
├── models.py                      # OrderAgentState, OrderEntities, OrderDraft
└── prompts/
    ├── entity_extract_v1.md       # Versioned entity extraction prompt (12 few-shot)
    └── system_v1.md               # Order Agent system prompt with NLP rules

bi_agent/
├── main.py                        # FastAPI app factory, port 8003, lifespan
├── graph.py                       # LangGraph: schema→nl2sql→safety→execute→format
├── nodes/
│   ├── schema_explorer.py         # pg_catalog introspection, allowlist from config
│   ├── nl2sql.py                  # GPT-4o, NL2SQL with schema + 6 few-shot
│   ├── safety_check.py            # SELECT-only enforcement, pattern blocklist
│   ├── execute_query.py           # Tool Registry: bi__run_query
│   └── format_response.py         # GPT-4o-mini, human-readable result summary
├── a2a_server.py                  # POST /a2a/tasks, GET /a2a/tasks/{id}
├── schema_config.yaml             # Table allowlist, business glossary
├── models.py                      # BIAgentState, BIQueryResult
└── prompts/
    ├── nl2sql_v1.md               # Versioned NL2SQL prompt (6 few-shot)
    └── format_response_v1.md      # Result formatting prompt

shared/                            # Extends existing shared/ from feature 001
├── auth_context.py                # (existing) ContextVar token forwarding
├── tool_registry_client.py        # (existing) ToolRegistryClient
├── llm_client.py                  # NEW: OpenAI async wrapper, model routing, retry
└── a2a/
    ├── __init__.py
    ├── models.py                  # A2ATask, AgentCard, ExecutionPlan, TaskStatus
    ├── server.py                  # A2A task store (Redis-backed), endpoint helpers
    └── client.py                  # A2A task submit + poll client

config/
├── tools.yaml                     # (existing, feature 001)
└── bi_schema.yaml                 # NEW: Table allowlist and business glossary for BI Agent

tests/002-orchestrator-domain-agents/
├── contract/
│   ├── test_chat_contract.py          # POST /chat response schema
│   ├── test_a2a_contract.py           # A2A task lifecycle contract
│   ├── test_order_agent_contract.py   # Order Agent A2A response schema
│   └── test_bi_agent_contract.py      # BI Agent A2A response schema
├── integration/
│   ├── test_intent_routing.py         # Intent classify → correct agent stub
│   ├── test_order_flow.py             # Full order conversation multi-turn
│   ├── test_bi_query.py               # BI NL2SQL → execute → format
│   ├── test_a2a_lifecycle.py          # Task submitted→working→completed
│   └── test_token_forwarding.py       # Bearer token through full chain
└── unit/
    ├── test_vn_utils.py               # Vietnamese NLP utils (50 test cases)
    ├── test_product_matcher.py        # FAISS search, threshold routing
    ├── test_orchestrator_plan.py      # Plan generation, replanning
    └── test_nl2sql.py                 # SQL generation correctness

evals/
├── runner.py                          # Eval runner (from Agentic_WorkPlan_v2.md §5.3)
└── cases/
    ├── intent_classification.yaml     # 20 cases
    ├── entity_extraction.yaml         # 20 cases
    ├── nl2sql.yaml                    # 20 cases
    └── e2e_order.yaml                 # 20 end-to-end cases
```

**Structure Decision**: 3-service layout (orchestrator/, order_agent/, bi_agent/) + extended shared/ library + tests/002-* mirror. Each service is an independently deployable Docker container. `shared/a2a/` is a new shared module for A2A protocol models and helpers used by all three services.

---

## Phase 0: Research Findings

*See [research.md](research.md) for full details.*

**Key decisions**:
1. **LangGraph + Redis checkpointer**: `AsyncRedisSaver` with `thread_id=session_id` for re-entrant state. Human-in-the-loop via `graph.astream(input, config, interrupt_before=["confirm"])`.
2. **A2A HTTP polling**: Lightweight `POST /a2a/tasks` + `GET /a2a/tasks/{id}` with Redis-backed task store. Tasks stored as JSON with 1-hour TTL. Orchestrator polls at 0.5s intervals up to 30s timeout.
3. **FAISS + multilingual embeddings**: `paraphrase-multilingual-MiniLM-L12-v2` for Vietnamese product names. Cosine similarity thresholds: ≥ 0.85 auto-select, 0.65–0.84 GPT-4o-mini rerank, < 0.65 ask user. Index rebuilt every 30 minutes in background thread.
4. **faster-whisper**: `large-v3` model with `compute_type="int8"` for CPU. Audio normalized to 16kHz WAV before transcription. Vietnamese WER ~10-12% on clear speech.
5. **Model routing**: GPT-4o-mini for latency-sensitive structured tasks (< 1s budget). GPT-4o for planning and NL2SQL. Escalation from GPT-4o-mini when `confidence < 0.72`.

---

## Phase 1: Design Artifacts

*See [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md) for full details.*

### Key Design Decisions

**Orchestrator Graph**:
```
classify_intent → plan_execution → dispatch_to_agent → aggregate_response
                        ↑                   |
                    [replan]  ←—— [agent_failed] ←——
```
- State persisted to Redis after every node via LangGraph checkpointer
- Plan persisted immediately after `plan_execution` node (Constitution V)
- Max 3 replanning attempts (`MAX_REPLAN_ATTEMPTS = 3`)
- Chitchat handled directly in `aggregate_response` without A2A dispatch

**Order Agent Graph**:
```
extract_entities → match_products → check_customer → build_preview
                                                          ↓
                                               [interrupt: confirm]
                                                          ↓
                                                   submit_order
```
- `interrupt_before=["submit_order"]` for mandatory confirmation gate
- Product matching uses FAISS index loaded at startup, refreshed every 30 min
- Multi-turn state preserved in LangGraph checkpoint (session_id = A2A task_id)

**BI Agent Graph**:
```
explore_schema → generate_sql → check_safety → execute_query → format_response
```
- `check_safety` rejects any non-SELECT SQL before execution
- Schema context (table allowlist + business glossary) injected into NL2SQL prompt
- Result formatted with GPT-4o-mini for human-readable output

**Prompt Versioning**:
All prompts stored in `{service}/prompts/{name}_v{N}.md`. Active version referenced in code as constant. Version increment required for any modification that changes behavior.

**A2A Task Store**:
- Redis hash: `a2a:task:{task_id}` → `{status, skill, params, result, created_at, updated_at}`
- 1-hour TTL on completed/failed tasks
- Task ID = `uuid4()` string
- Session resume: A2A task_id used as LangGraph `thread_id` for Order Agent multi-turn

**Token Budget Tracking** (Constitution §LLM-Powered Agents):
- Each LLM call records `prompt_tokens`, `completion_tokens` in structured log
- Fields: `agent_id`, `agent_role`, `model`, `prompt_tokens`, `completion_tokens`, `trace_id`

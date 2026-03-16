# agentic-saas Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-16

## Active Technologies
- Python 3.12 + FastAPI 0.111+, LangGraph 0.2+, LangChain, langchain-openai, langgraph-checkpoint-redis, httpx, sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu, faster-whisper, asyncpg, pydantic v2, pytest, pytest-asyncio, respx, ruff (002-orchestrator-domain-agents)
- Redis (LangGraph checkpointer + session state + A2A task store); PostgreSQL (analytics DB for BI queries — already deployed); No new DB schema for v1 (002-orchestrator-domain-agents)

- **Python 3.12** + FastAPI 0.111+, Pydantic v2, httpx (async) — all services
- **LangGraph / LangChain** — Orchestrator and Domain Agent reasoning pipelines
- **PostgreSQL** — long-term memory and analytics data (asyncpg driver)
- **pgvector** — vector similarity search (product embeddings, semantic retrieval)
- **Redis** — short-term memory, LangGraph checkpointer, session state
- **YAML config** — Tool Registry v1 tool definitions (`config/tools.yaml`)

## Project Structure

```text
tool_registry/          # FastAPI service, port 8001 (Tool Registry)
orchestrator/           # FastAPI service, port 8000 (Orchestrator Agent)
order_agent/            # FastAPI service, port 8002 (Order Domain Agent)
bi_agent/               # FastAPI service, port 8003 (BI Domain Agent)
shared/                 # Cross-service: auth_context.py, tool_registry_client.py
config/                 # tools.yaml, .env.example
tests/                  # Mirrors service structure: contract/ integration/ unit/
specs/                  # Feature specs, plans, data models, contracts
```

## Commands

```bash
make dev                          # Start all 4 services via Docker Compose
pytest tests/001-tool-registry/   # Run Tool Registry tests
pytest tests/001-tool-registry/contract/  # Contract tests first
ruff check .                      # Lint
ruff format .                     # Format
```

## Code Style

- Python 3.12: type hints everywhere, Pydantic v2 models for all I/O
- Async throughout: `async def` + `await`, `httpx.AsyncClient`, `asyncpg`
- ContextVar for request-scoped state (Bearer token, tenant_id) — never thread-local
- Error messages in English

## Active Features

- **001-tool-registry**: Tool Registry v1 — YAML config, dual dispatch (HTTP + handler),
  hot-reload, ToolRegistryClient shared lib. Sprint 1 Days 3–4.

## Constitution Principles (quick ref)

- Every tool/capability MUST be registered in Tool Registry before use
- A2A protocol for all agent-to-agent task delegation
- MCP layer for all LLM tool access
- Bearer tokens forwarded via ContextVar — NEVER stored or logged
- Test-First: contract tests written before implementation

<!-- MANUAL ADDITIONS START -->
## Orchestrator Service (002) — port 8000

**Start locally:**
```bash
REDIS_URL=redis://localhost:6379/0 uvicorn orchestrator.main:app --port 8000 --reload
```

**Key files:**
- [orchestrator/main.py](orchestrator/main.py) — FastAPI app, `/chat` and `/voice` endpoints, lifespan
- [orchestrator/graph.py](orchestrator/graph.py) — `invoke_chat()` pipeline: classify → plan → dispatch → aggregate
- [orchestrator/nodes/intent_classify.py](orchestrator/nodes/intent_classify.py) — GPT-4o-mini intent classifier, escalates to GPT-4o at confidence < 0.72
- [orchestrator/nodes/plan.py](orchestrator/nodes/plan.py) — ExecutionPlan generation, persists to Redis immediately
- [orchestrator/nodes/a2a_dispatch.py](orchestrator/nodes/a2a_dispatch.py) — submits A2A tasks, polls results, handles replan
- [orchestrator/nodes/aggregate.py](orchestrator/nodes/aggregate.py) — synthesizes Domain Agent results into Vietnamese reply
- [orchestrator/session.py](orchestrator/session.py) — Redis-backed session store (30-min TTL, last-3-turns history)
- [orchestrator/stt.py](orchestrator/stt.py) — WhisperSTT with faster-whisper large-v3, int8, vad_filter=True

**Run tests:**
```bash
pytest tests/002-orchestrator-domain-agents/
pytest tests/002-orchestrator-domain-agents/contract/
pytest tests/002-orchestrator-domain-agents/integration/
pytest tests/002-orchestrator-domain-agents/unit/
```

---

## Order Agent Service (002) — port 8002

**Start locally:**
```bash
REDIS_URL=redis://localhost:6379/0 TOOL_REGISTRY_URL=http://localhost:8001 uvicorn order_agent.main:app --port 8002 --reload
```

**Key files:**
- [order_agent/main.py](order_agent/main.py) — FastAPI app, A2A router, lifespan
- [order_agent/graph.py](order_agent/graph.py) — `run_order_graph()`: extract → match → customer → preview → confirm → submit
- [order_agent/a2a_server.py](order_agent/a2a_server.py) — `POST /a2a/tasks`, `GET /a2a/tasks/{id}`, continuation resume logic
- [order_agent/product_matcher.py](order_agent/product_matcher.py) — FAISS IndexFlatIP cosine similarity, atomic refresh
- [order_agent/vn_utils.py](order_agent/vn_utils.py) — Vietnamese NLP: normalize, strip_honorifics, words_to_numbers
- [order_agent/nodes/](order_agent/nodes/) — extract_entities, match_products, check_customer, preview, confirm, submit_order

**Draft persistence key:** `order:draft:{task_id}` (1-hour TTL)
**Checkpoint resume:** POST /a2a/tasks with `params.continuation.task_id` resumes interrupted confirmation

---

## BI Agent Service (002) — port 8003

**Start locally:**
```bash
REDIS_URL=redis://localhost:6379/0 TOOL_REGISTRY_URL=http://localhost:8001 uvicorn bi_agent.main:app --port 8003 --reload
```

**Key files:**
- [bi_agent/main.py](bi_agent/main.py) — FastAPI app, A2A router, lifespan
- [bi_agent/graph.py](bi_agent/graph.py) — `run_bi_graph()`: schema_explorer → nl2sql → safety_check → execute → format
- [bi_agent/nodes/safety_check.py](bi_agent/nodes/safety_check.py) — SELECT-only enforcement + LIMIT 500 injection
- [bi_agent/nodes/nl2sql.py](bi_agent/nodes/nl2sql.py) — GPT-4o with schema context injection, temperature=0
- [config/bi_schema.yaml](config/bi_schema.yaml) — table allowlist + business glossary for NL2SQL prompt

**Eval runner:**
```bash
python -m evals.runner --all
python -m evals.runner --suite intent_classification --base-url http://localhost:8000
```

---

## Tool Registry Service (001)

**Start locally:**
```bash
TOOLS_YAML_PATH=config/tools.yaml uvicorn tool_registry.main:create_app --factory --port 8001 --reload
```

**Run tests:**
```bash
pytest tests/001-tool-registry/                          # all tests (90 total)
pytest tests/001-tool-registry/contract/                 # contract tests only
pytest tests/001-tool-registry/unit/                     # unit tests only
pytest tests/001-tool-registry/integration/              # integration tests only
```

**Key files:**
- `tool_registry/dispatch.py` — routes to HTTP adapter or Python handler
- `tool_registry/handlers/bi_query_handler.py` — asyncpg SELECT-only executor
- `shared/tool_registry_client.py` — `ToolRegistryClient` for agent consumers
- `shared/auth_context.py` — ContextVar token forwarding + `AuthForwardMiddleware`
- `config/tools.yaml` — tool definitions (hot-reload on change, <5s)
<!-- MANUAL ADDITIONS END -->

## Recent Changes
- 002-orchestrator-domain-agents: Added Python 3.12 + FastAPI 0.111+, LangGraph 0.2+, LangChain, langchain-openai, langgraph-checkpoint-redis, httpx, sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), faiss-cpu, faster-whisper, asyncpg, pydantic v2, pytest, pytest-asyncio, respx, ruff

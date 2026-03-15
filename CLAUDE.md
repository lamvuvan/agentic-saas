# agentic-saas Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-16

## Active Technologies

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

# Implementation Plan: Tool Registry

**Branch**: `001-tool-registry` | **Date**: 2026-03-16 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-tool-registry/spec.md`

## Summary

Build the Tool Registry v1: a FastAPI service (port 8001) that catalogs tool definitions
from a YAML config file, exposes them for discovery via `GET /tools` (OpenAI function-call
format), and executes them via `POST /tools/{name}/execute` using an HTTP adapter (for
API-backed tools) or a Python handler (for runtime-direct tools like `bi_query_handler`).
Bearer token auth is forwarded via `ContextVar` — never stored. Config hot-reloads on
file change within 5 s. Accompanied by a `ToolRegistryClient` shared library consumed
by all agents (Orchestrator, Order Agent, BI Agent).

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI 0.111+, Pydantic v2, httpx (async HTTP adapter),
watchdog (YAML hot-reload), PyYAML, asyncpg (BI handler — direct PostgreSQL), redis
(short-term memory, shared layer — not used inside registry itself), langchain/langgraph
(agent consumers — not used inside registry itself)
**Storage**: `config/tools.yaml` (YAML file — no database for tool definitions in v1).
PostgreSQL used by the broader system (long-term memory, data). pgvector (vector search,
broader system). Redis (short-term memory/session, broader system). The registry service
is stateless — YAML only.
**Testing**: pytest, pytest-asyncio, httpx `AsyncClient` (test client)
**Target Platform**: Linux container, Docker Compose, port 8001
**Project Type**: Internal web-service (MCP-style HTTP API)
**Performance Goals**: `GET /tools` < 50 ms p95 (in-memory). `POST .../execute` < 10 s
total (backend-bound; registry overhead < 20 ms). Hot-reload < 5 s from file write.
**Constraints**: Token MUST NOT be stored or logged. YAML is sole config store in v1.
Hot-reload MUST be atomic (no partial-state window). Stateless between requests.
**Scale/Scope**: ~10 tools across 3 namespaces (customer, order, bi) at MVP.
Single-instance Docker Compose deployment.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Agent-First Design | ✅ Pass | Tool Registry is a discrete, single-responsibility service. ToolRegistryClient is a separate shared-lib artifact. No dual responsibilities. |
| II. Contract-Driven / Tool Registry | ✅ Pass (bootstrapping note) | Registry IS the central Tool Registry — it is the foundational layer; other services register tools WITH it. The registry's own endpoints are its contract. A2A Agent Card for the registry MUST be created as a foundational task. MCP-style HTTP is the wire protocol. |
| III. Test-First | ✅ Pass | Contract tests (GET /tools schema, execute request/response) MUST be written before implementation begins. Unit tests for config_loader, dispatch, and client are required. |
| IV. Observability | ✅ Pass | Structured JSON logs MUST be emitted at every execute entry/exit: agent_id, trace_id, tenant_id, action, status, duration_ms. Shared `AuthForwardMiddleware` + logging middleware cover this. |
| V. Resilience | ✅ Pass | 10 s timeout + 2 retries on HTTP adapter. Hot-reload is atomic (staging swap). ToolRegistryClient raises descriptive exception on registry unavailability. YAML last-good-config retained if file is deleted. |
| VI. Multi-Tenancy | ⚠️ Partial — Justified | v1 forwards `X-Tenant-Id` to backends but has no per-tenant tool visibility isolation or access policies. Deferred to v2 per work plan (Week 4–5 backlog). See Complexity Tracking. |
| VII. Simplicity | ✅ Pass | YAML-only, no DB, no premature abstraction. Dual dispatch (HTTP + handler) is the minimum needed for the `bi_query_handler` use case. |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| Principle VI partial: no per-tenant tool isolation | v1 MVP scope — work plan explicitly defers tenant isolation to v2 (Week 4–5 backlog). Token forwarding via `X-Tenant-Id` is the only tenant signal in v1. | Full per-tenant policy engine requires a database and access policy store — contradicts v1 YAML-only philosophy and Sprint 1 timeline. |
| Dual dispatch (HTTP adapter + Python handler) | `bi__run_query` requires direct asyncpg PostgreSQL access; cannot be expressed as an HTTP api-block without a wrapper service. | A wrapper HTTP service around PostgreSQL adds a new network hop and deployment unit — more complexity, not less. |

## Project Structure

### Documentation (this feature)

```text
specs/001-tool-registry/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── get-tools.json          # GET /tools response schema
│   ├── execute-tool.json       # POST /tools/{name}/execute request + response
│   └── health.json             # GET /health response schema
└── tasks.md             # Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
tool_registry/                   # FastAPI service (port 8001)
├── main.py                      # App factory, middleware wiring, lifespan hook
├── config_loader.py             # YAML load, Pydantic validation, atomic hot-reload
├── models.py                    # ToolDefinition, ApiBlock, ExecRequest, ExecResponse
├── dispatch.py                  # Dual-path executor: HTTP adapter | handler dispatch
├── auth_context.py              # ContextVar[token, tenant_id] + AuthForwardMiddleware
├── handlers/
│   └── bi_query_handler.py      # Direct asyncpg PostgreSQL SELECT handler
└── routers/
    ├── tools.py                 # GET /tools, POST /tools/{name}/execute
    └── health.py                # GET /health

shared/
├── auth_context.py              # Canonical shared copy (symlinked into each service)
└── tool_registry_client.py      # ToolRegistryClient: get_openai_tools(), execute()

config/
└── tools.yaml                   # Tool definitions (customer, order, bi namespaces)

tests/001-tool-registry/
├── contract/
│   ├── test_get_tools_contract.py     # Response matches get-tools.json schema
│   └── test_execute_contract.py       # Request/response match execute-tool.json schema
├── integration/
│   ├── test_hotreload.py              # File-change → active within 5 s
│   ├── test_http_dispatch.py          # HTTP adapter against respx mock backend
│   ├── test_handler_dispatch.py       # bi_query_handler against mock asyncpg
│   └── test_client.py                 # ToolRegistryClient end-to-end
└── unit/
    ├── test_config_loader.py          # YAML parse, duplicate detection, bad entries
    ├── test_dispatch.py               # Path selection, timeout enforcement, retry
    ├── test_auth_context.py           # ContextVar isolation across concurrent requests
    └── test_tool_registry_client.py   # OpenAI format conversion, token forwarding
```

**Structure Decision**: Single-project layout. `tool_registry/` is the FastAPI service
package; `shared/` holds cross-service artifacts consumed by all four services.
Tests are co-located under `tests/001-tool-registry/` with contract → integration →
unit layers per Principle III.

# Feature Specification: Tool Registry

**Feature Branch**: `001-tool-registry`
**Created**: 2026-03-16
**Status**: Draft
**Input**: User description: "tool registry"

## Overview

The Tool Registry is the authoritative, runtime-queryable catalog of every callable
capability in the Agentic SaaS platform. It enables Orchestrator and Domain Agents to
discover and invoke tools without hard-coded endpoints.

**v1 philosophy (this plan)**: Simplest sufficient implementation — tool definitions
stored in a YAML config file, loaded at startup with hot-reload on file change. No
database, no web UI, no token storage. v2 (PostgreSQL, Admin Web UI, audit log,
version history) is explicitly post-MVP backlog.

## Clarifications

### Session 2026-03-16

- Q: Implementation scope — v1 vs full vision → A: v1 only — YAML config, no DB, no UI,
  hot-reload. Simplify spec to match Sprint 1 Day 3 scope (Agentic_WorkPlan_v2.md §2).
- Q: Custom handler scope → A: Yes — `handler` field supported alongside `api` block;
  registry dispatches to registered Python functions (required for `bi__run_query`).
- Q: Auth enforcement at registry level → A: No enforcement — registry forwards any
  request; backend is responsible for rejecting unauthenticated calls.
- Q: ToolRegistryClient scope → A: Same feature — `ToolRegistryClient` shared library
  is a deliverable of this plan alongside the registry service.
- Q: Error message language → A: English for all errors (backend domain errors and
  system/infrastructure errors alike).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Register a Tool via YAML Config (Priority: P1)

An agent developer adds a new tool capability by editing `config/tools.yaml`. The
definition includes: tool name, namespace, description, input parameter schema, and the
backend API mapping. On next hot-reload cycle (or service restart), the tool is available
for discovery and execution.

**Why this priority**: YAML-based registration is the entire registration mechanism for
v1. Without it there are no tools in the system.

**Independent Test**: Add a tool entry to `config/tools.yaml`, trigger hot-reload, then
call `GET /tools` and confirm the new tool appears with all its attributes intact.

**Acceptance Scenarios**:

1. **Given** a valid tool definition added to `config/tools.yaml`, **When** the hot-reload
   detects the file change, **Then** `GET /tools` returns the new tool immediately (within
   the reload interval) without a service restart.

2. **Given** a tool definition with a malformed schema in `config/tools.yaml`, **When**
   the loader processes the file, **Then** the service logs a descriptive error and skips
   the invalid entry; all other tools remain available.

3. **Given** two tools with the same `name` field, **When** the loader processes the
   YAML, **Then** the loader rejects the duplicate and logs an error identifying the
   conflicting entry.

---

### User Story 2 - Discover Tools at Runtime (Priority: P1)

An Orchestrator or Domain Agent queries `GET /tools` (optionally filtered by namespace)
to retrieve the list of available tools in OpenAI function-call format, then decides
which tool to invoke.

**Why this priority**: Runtime discovery replaces hard-coded tool endpoints. This is the
core value of the registry for all consuming agents.

**Independent Test**: Register two tools in different namespaces, query by each namespace,
confirm only the matching tools are returned in each response.

**Acceptance Scenarios**:

1. **Given** multiple tools registered across namespaces, **When** an agent calls
   `GET /tools?namespace=customer`, **Then** only tools in the `customer` namespace are
   returned in OpenAI function-call schema format.

2. **Given** a valid query with no namespace filter, **When** an agent calls `GET /tools`,
   **Then** all registered tools are returned.

3. **Given** a valid query, **When** the registry responds, **Then** each tool entry
   includes: name, namespace, description, and parameter schema.

---

### User Story 3 - Execute a Tool (Priority: P1)

A Domain Agent calls `POST /tools/{name}/execute` with a JSON payload. The Tool Registry
forwards the call to the configured backend API using the tool's `api` mapping block,
injects the Bearer token from the request's `Authorization` header (via ContextVar —
never stored), and returns the response. This acts as an authenticated proxy layer.

**Why this priority**: The registry is not discovery-only in v1 — it also executes tools
on behalf of agents. Without execution, agents cannot perform any domain work.

**Independent Test**: Register a tool with a mock backend URL, call
`POST /tools/customer__get_customers/execute` with a Bearer token header and a valid
payload, confirm the registry forwards the call and returns the backend response.

**Acceptance Scenarios**:

1. **Given** a registered tool and a valid Bearer token in the `Authorization` header,
   **When** an agent calls `POST /tools/{name}/execute` with a valid payload, **Then**
   the registry forwards the call to the configured backend URL with the token in the
   `Authorization` header and returns the response.

2. **Given** an execution request, **When** the registry processes it, **Then** the Bearer
   token is read from the request context (ContextVar), forwarded once to the backend,
   and discarded — it is NEVER written to disk, logs, or any persistent store.

3. **Given** an execution request for a non-existent tool name, **When** the agent calls
   `POST /tools/{name}/execute`, **Then** the registry returns a 404 error with a
   descriptive message.

4. **Given** the configured backend API returns an error, **When** the registry receives
   it, **Then** the registry maps it to a user-friendly error message and returns it to
   the calling agent.

---

### User Story 4 - Service Health Check (Priority: P2)

The Docker Compose orchestrator and dependent services call `GET /health` to verify the
Tool Registry service is running and its YAML config loaded successfully.

**Why this priority**: Enables Docker Compose dependency health-gating and allows
Orchestrators to detect registry unavailability.

**Independent Test**: Start the service, call `GET /health`, confirm `200 OK` with a
body indicating tool count loaded. Stop YAML loading (invalid file), confirm health
returns a degraded/error status.

**Acceptance Scenarios**:

1. **Given** the service is running with a valid `tools.yaml`, **When** `GET /health` is
   called, **Then** the response is `200 OK` with body including the count of loaded tools.

2. **Given** `tools.yaml` has a fatal parse error, **When** `GET /health` is called,
   **Then** the response indicates the error condition (non-200 or degraded status).

---

### User Story 5 - ToolRegistryClient Library (Priority: P1)

An Orchestrator or Domain Agent uses the `ToolRegistryClient` shared library to discover
and execute tools without writing HTTP client code. The client handles URL construction,
OpenAI format conversion, and Bearer token forwarding transparently.

**Why this priority**: Agents depend on this client — not the raw HTTP API — as their
interface to the registry. Without it, every agent must independently implement token
forwarding and format conversion, creating duplication and drift risk.

**Independent Test**: Instantiate `ToolRegistryClient` against a running registry,
call `get_openai_tools("customer")`, confirm the returned list matches OpenAI function
schema. Call `execute("customer__get_customers", {"query": "Lâm"})` with a mock token
header, confirm the registry receives and forwards the token.

**Acceptance Scenarios**:

1. **Given** a running registry with loaded tools, **When** a caller invokes
   `client.get_openai_tools(namespace="customer")`, **Then** the client returns a list of
   tool definitions in OpenAI function-call format for the `customer` namespace only.

2. **Given** a caller with a Bearer token in context, **When** `client.execute(name, params)`
   is called, **Then** the client forwards the call to `POST /tools/{name}/execute` with
   the `Authorization: Bearer <token>` header set and returns the response.

3. **Given** the registry is unavailable, **When** the client attempts a call, **Then**
   the client raises a descriptive exception within the timeout window (does not hang).

---

### Edge Cases

- What if `tools.yaml` is edited during a high-traffic period? Hot-reload MUST be
  atomic — agents in mid-execution MUST see a consistent tool set for their request
  duration; the new set takes effect only for new requests.
- What if the backend API call times out during tool execution? The registry MUST return
  a timeout error to the calling agent within 10 seconds (configurable).
- What if the `Authorization` header is absent when calling `POST /tools/{name}/execute`?
  The registry MUST forward the request as-is; enforcing auth is the backend's
  responsibility, not the registry's.
- What if `tools.yaml` is deleted? The service MUST retain the last successfully loaded
  config and emit a log warning; it MUST NOT clear all tools.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The registry MUST load tool definitions from `config/tools.yaml` at startup
  and expose them for discovery and execution.
- **FR-002**: The registry MUST detect changes to `config/tools.yaml` and hot-reload
  tool definitions without requiring a service restart.
- **FR-003**: The registry MUST expose `GET /tools` returning all registered tools in
  OpenAI function-call schema format; MUST support optional `?namespace=<value>` filter.
- **FR-004**: The registry MUST expose `POST /tools/{name}/execute` to execute a named
  tool via one of two dispatch paths determined by the tool's definition:
  (a) **HTTP dispatch** — forwards the call to a configured backend URL using the `api`
  block (method, URL, parameter mapping, response mapping); or
  (b) **Handler dispatch** — invokes a registered Python function named by the `handler`
  field (used for tools requiring direct runtime access, e.g., `bi_query_handler` for
  PostgreSQL execution). Both paths MUST support Bearer token forwarding via ContextVar.
- **FR-005**: The registry MUST forward the Bearer token from the incoming request's
  `Authorization` header to the backend API call; the token MUST be read via ContextVar
  and MUST NOT be stored, logged, or persisted in any form. The registry MUST NOT enforce
  authentication itself — requests without a token are forwarded as-is; auth enforcement
  is the backend's responsibility.
- **FR-006**: The registry MUST forward `X-Tenant-Id` (if present) to the backend API
  call alongside the Bearer token.
- **FR-007**: The registry MUST reject duplicate tool names at load time with a
  descriptive log error; the duplicate entry MUST NOT be loaded.
- **FR-008**: The registry MUST enforce a 10-second timeout on backend API calls made
  during tool execution, with a maximum of 2 retries before returning an error.
- **FR-008a**: All error messages returned by the registry (backend failures, timeouts,
  tool-not-found, config errors) MUST be in English.
- **FR-009**: The registry MUST expose `GET /health` returning service status and the
  count of currently loaded tools.
- **FR-010**: The registry MUST map backend API responses to a normalized output using
  the `response_path` and `response_rename` mappings defined in the tool's `api` block.
- **FR-011**: The `ToolRegistryClient` library MUST implement `get_openai_tools(namespace)`
  converting `GET /tools` responses into OpenAI function-call schema format.
- **FR-012**: The `ToolRegistryClient` library MUST implement `execute(name, params)`
  forwarding the active Bearer token via the `Authorization` header on every call to
  `POST /tools/{name}/execute`.

### Key Entities

- **Tool Definition** (YAML-sourced): The configuration record for a callable tool.
  Fields: `name` (unique, `namespace__verb` format), `namespace`, `description`,
  `parameters` (JSON Schema object), `api` block (`method`, `url`, `params`/`body_mapping`,
  `response_path`, `response_rename`, `response_fields`). Alternatively, a `handler`
  field names a custom Python handler instead of an `api` block.
- **Auth Context** (request-scoped): Bearer token and tenant ID extracted from incoming
  request headers and stored in ContextVar for the duration of that request only.
  Destroyed automatically when the request completes.
- **Execution Request**: The payload sent by a calling agent to
  `POST /tools/{name}/execute`. Fields: parameter values matching the tool's declared
  schema.
- **Execution Response**: The normalized response returned to the calling agent after
  backend mapping. Includes the mapped response fields or an error object.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `GET /tools` returns the full tool list in under 50 milliseconds (config
  is in-memory; no I/O on read path).
- **SC-002**: `POST /tools/{name}/execute` completes within 10 seconds for any tool
  (timeout enforced server-side); backend latency is the dominant factor.
- **SC-003**: Hot-reload detects and applies `tools.yaml` changes within 5 seconds of
  a file modification.
- **SC-004**: 100% of tool execution requests forward the Bearer token to the backend
  and 0% of requests result in the token being written to any persistent store or log.
- **SC-005**: The service starts and loads all tools from `tools.yaml` in under 3 seconds
  (measured from process start to first healthy `GET /health` response).
- **SC-006**: All 3 built-in tool namespaces (customer, order, BI) load correctly and
  pass integration tests in CI: customer/order tools via HTTP dispatch against a mock
  backend, BI tool via handler dispatch against a mock PostgreSQL connection.

## Assumptions

- Tool definitions are managed by editing `config/tools.yaml` directly; there is no
  registration API in v1.
- Backend API URLs use environment variable interpolation (`${ENV_VAR}`) resolved at
  load time from the process environment.
- The `ToolRegistryClient` shared library is an in-scope deliverable of this feature
  (not a separate feature). It provides two operations: `get_openai_tools(namespace)`
  — calls `GET /tools` and converts the response to OpenAI function-call format; and
  `execute(name, params)` — calls `POST /tools/{name}/execute` forwarding the Bearer
  token via `Authorization` header. Orchestrator and Domain Agents consume this client
  rather than calling the registry HTTP API directly.
- The service runs on port 8001 in Docker Compose alongside the Orchestrator (8000),
  Order Agent (8002), and BI Agent (8003).
- Custom handlers (e.g., `bi_query_handler` for direct PostgreSQL execution) are in scope
  as an extension point alongside `api`-block tools.

## Out of Scope (v1)

- Web UI or CLI dashboard for browsing the registry.
- Database-backed persistence of tool definitions (v2 backlog).
- Audit log persistence for registrations/changes (v2 backlog).
- Per-tool access-control policies beyond Bearer token forwarding (v2 backlog).
- Tool health status tracking and drift detection vs A2A Agent Cards (v2 backlog).
- Governed deprecation/deregistration workflows (v2 backlog).
- Multi-tenant tool visibility isolation (v2 backlog).
- Multi-region replication.
- Billing/cost attribution per tool invocation.

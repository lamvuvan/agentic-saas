# Research: Tool Registry v1

**Feature**: `001-tool-registry` | **Date**: 2026-03-16
**Status**: Complete — all NEEDS CLARIFICATION resolved

---

## Decision 1: YAML Hot-Reload Mechanism

**Decision**: Use `watchdog` library (inotify/FSEvents-based file system events) with an
atomic config swap pattern. A background thread watches `config/tools.yaml`; on
`FileModifiedEvent` the loader parses the new YAML into a staging `ToolStore` object,
validates it fully, then atomically replaces the active reference with
`threading.Event` + module-level `_active_store` swap.

**Rationale**: `watchdog` is event-driven (sub-second detection on Linux inotify and
macOS FSEvents) rather than polling, which avoids busy-wait CPU overhead. The staging
swap pattern ensures the service never serves a partially-loaded config — agents either
see the old valid config or the new valid config, never a mix.

**Alternatives considered**:
- Polling (`asyncio` loop checking `mtime` every N seconds): Simple but introduces up
  to N-second lag and wastes CPU. Rejected.
- SIGHUP reload: Clean but requires process signal infrastructure not available in
  Docker Compose without extra setup. Rejected for v1.
- Kubernetes ConfigMap watch: Out of scope — single Docker Compose deployment in v1.

---

## Decision 2: Auth Context — ContextVar Pattern

**Decision**: Use Python `contextvars.ContextVar` for request-scoped Bearer token
and tenant ID storage. A FastAPI middleware (`AuthForwardMiddleware`) extracts the
`Authorization` and `X-Tenant-Id` headers on every request, calls `set_auth(token,
tenant_id)`, and the ContextVar is automatically isolated per async task context.
Token is never written to any persistent store, log output, or response body.

**Rationale**: `ContextVar` is the correct primitive for request-scoped async state in
Python. Unlike threading.local (unsafe in async code) and unlike global variables
(no isolation), ContextVar provides automatic per-task isolation — concurrent requests
each get their own copy. This satisfies the "token lives in ContextVar, dies with the
request" design from `Agentic_WorkPlan_v2.md` §2.2 exactly.

**Alternatives considered**:
- FastAPI `Request` dependency injection: Would require threading `request` through every
  function call to `dispatch.py` and handlers. Verbose and error-prone. Rejected.
- Thread-local storage: Unsafe with asyncio (async code can switch tasks mid-function).
  Rejected.
- Redis session storage: Would store the token — violates the "never store" requirement.
  Rejected.

---

## Decision 3: Dual Dispatch Architecture

**Decision**: `dispatch.py` inspects the loaded `ToolDefinition` for the presence of
either an `api` field or a `handler` field (mutually exclusive, validated at load time):

- **HTTP dispatch** (`api` field present): Build the HTTP request from the `api` block
  (`method`, `url` with env-var interpolation, `params`/`body_mapping`), execute via
  `httpx.AsyncClient` with `timeout=10.0` and `max_retries=2`, apply `response_path`
  and `response_rename` to normalize the response.
- **Handler dispatch** (`handler` field present): Resolve the handler name to a
  registered Python callable from a `HANDLER_REGISTRY` dict populated at startup.
  Call the handler with the execution params and auth context. Handlers are async
  functions with signature `async def handler(params: dict, auth: AuthContext) -> dict`.

**Rationale**: Both paths are needed for MVP: all customer/order tools use HTTP dispatch
(the API), while `bi__run_query` requires direct asyncpg access. A unified dispatch
interface keeps `routers/tools.py` clean — it calls `dispatch(tool, params)` regardless
of the underlying path.

**Alternatives considered**:
- Separate endpoints for HTTP vs handler tools: Would leak implementation detail into
  the API surface. Agents would need to know which type each tool is. Rejected.
- Always HTTP (wrap PostgreSQL in a microservice): Adds a deployment unit and network
  hop for no benefit. Rejected.

---

## Decision 4: OpenAI Function-Call Schema Conversion

**Decision**: `GET /tools` returns tools in OpenAI function-call format:

```json
[
  {
    "type": "function",
    "function": {
      "name": "customer__get_customers",
      "description": "...",
      "parameters": {
        "type": "object",
        "required": ["query"],
        "properties": {
          "query": { "type": "string", "description": "..." },
          "limit": { "type": "integer" }
        }
      }
    }
  }
]
```

The conversion is done in `routers/tools.py` by mapping `ToolDefinition.name`,
`description`, and `parameters` into the OpenAI schema wrapper. No LLM-specific
logic lives in the registry itself — it produces the schema; the consuming LLM
(GPT-4o-mini/GPT-4o) uses it for function-calling decisions.

**Rationale**: The `ToolRegistryClient.get_openai_tools()` is the primary consumer; it
passes this list directly to the OpenAI `functions` or `tools` parameter. Matching the
OpenAI format at the registry avoids per-agent conversion logic.

**Alternatives considered**:
- Return raw YAML-derived schema and convert in each agent: Duplicates conversion code
  across all agents. Rejected.
- Return Anthropic tool-use format: The work plan specifies OpenAI GPT-4o as the LLM.
  Rejected for v1 (can add format parameter later).

---

## Decision 5: HTTP Adapter — Response Normalization

**Decision**: The HTTP adapter applies two normalization steps from the tool's `api` block:

1. **`response_path`**: Extracts a nested field from the backend response
   (e.g., `response_path: data` extracts `response["data"]`).
2. **`response_rename`**: Renames keys in the extracted object
   (e.g., `{contactNumber: phone, id: customer_id}` → renames fields for downstream
   agent consumption).
3. **`response_fields`**: If present, filters the response to only the listed fields.

All three steps run in sequence; missing `response_path` returns the full response body.

**Rationale**: Backend APIs (the backend) return responses with proprietary field names and
nested structures. Domain agents expect normalized field names. Keeping normalization in
the registry config (YAML) means agents never need to know backend schema details.

**Alternatives considered**:
- Transform in each Domain Agent: Leaks backend schema knowledge into agent code.
  Rejected.
- Separate transformation service: Over-engineering for v1. Rejected.

---

## Decision 6: ToolRegistryClient Token Forwarding

**Decision**: `ToolRegistryClient.execute()` reads the Bearer token from the `ContextVar`
(via `get_token()`) and sets `Authorization: Bearer <token>` on the outgoing `httpx`
request to the registry. The registry then forwards it to the backend. This means the
token propagates: calling agent → ToolRegistryClient → Tool Registry service → backend
API. At no point is it persisted.

If the ContextVar is empty (no token in context), the header is omitted — enforcing
auth is the backend's responsibility (per clarification Q3).

**Rationale**: Consistent with the "token lives in ContextVar" philosophy. The client
does not require callers to pass the token as a parameter — it reads from context,
making integration ergonomic and preventing token leakage via function signatures.

---

## Decision 7: YAML Config Schema

**Decision**: The YAML structure follows `Agentic_WorkPlan_v2.md` §2.1 exactly:

```yaml
tools:
  - name: <namespace>__<verb>        # double-underscore separator, unique
    namespace: <string>              # groups tools for filtered discovery
    description: >                   # multi-line, injected into LLM system prompt
      <text>
    parameters:                      # JSON Schema object
      type: object
      required: [<fields>]
      properties:
        <field>: { type: <type>, description: <str> }
    # Dispatch path A — HTTP adapter:
    api:
      method: GET | POST | PUT | DELETE
      url: "${ENV_VAR}/path"          # env-var interpolation at load time
      params: { <tool_param>: <backend_param> }     # for GET query params
      body_mapping: { <tool_param>: <backend_field> }  # for POST body
      response_path: <string>         # top-level key to extract from response
      response_rename: { <old>: <new> }
      response_fields: [<field>, ...]
    # Dispatch path B — Python handler (mutually exclusive with api):
    handler: <registered_handler_name>
```

Validation at load time: Pydantic model enforces mutual exclusivity of `api` vs
`handler`. Missing required fields → skip entry + log error. Duplicate `name` →
skip duplicate + log error.

---

## Resolved Clarifications Summary

| # | Topic | Resolution |
|---|-------|-----------|
| 1 | v1 scope | YAML-only, no DB, Sprint 1 deliverable |
| 2 | Custom handlers | In scope — `handler` field alongside `api` block |
| 3 | Auth enforcement | No enforcement at registry — backend's responsibility |
| 4 | ToolRegistryClient scope | Same feature, same branch |
| 5 | Error message language | English for all errors |

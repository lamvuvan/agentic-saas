# Data Model: Tool Registry v1

**Feature**: `001-tool-registry` | **Date**: 2026-03-16

---

## Overview

All entities in v1 are config-sourced (YAML) or request-scoped (ContextVar). There is no
database. The YAML file is the single source of truth for tool definitions; the in-memory
`ToolStore` is the runtime representation. Auth context exists only for the lifetime of
a single HTTP request.

---

## Entity 1: ToolDefinition

**Source**: Parsed from `config/tools.yaml` at startup + hot-reload
**Runtime store**: `ToolStore` (module-level dict, atomically swapped on reload)

```
ToolDefinition
├── name: str                       # Unique. Format: "{namespace}__{verb}"
│                                   # e.g., "customer__get_customers"
├── namespace: str                  # Grouping key for filtered discovery
│                                   # e.g., "customer", "order", "bi"
├── description: str                # Human/LLM-readable capability description
├── parameters: JsonSchemaObject    # JSON Schema (type=object) defining inputs
│   ├── type: "object"              # Always "object" at root
│   ├── required: list[str]         # Required parameter names
│   └── properties: dict[str, ...]  # Field definitions with type + description
└── dispatch: ApiBlock | HandlerRef # Mutually exclusive (validated at load)
```

**Validation rules**:
- `name` MUST be unique across all loaded tools. Duplicate → skip + log error.
- Exactly one of `api` or `handler` MUST be present (not both, not neither).
- `parameters.type` MUST be `"object"`.
- `parameters.required` fields MUST all appear in `parameters.properties`.

**State transitions**:
```
YAML file on disk
    │  (file write / hot-reload event)
    ▼
Staging parse (validation)
    │  (validation pass)
    ▼
Active ToolStore        ← all GET /tools reads come from here
    │  (next reload)
    ▼
Replaced atomically
```

---

## Entity 2: ApiBlock

**Source**: `api:` field in YAML tool definition
**Purpose**: Describes how to execute the tool via HTTP dispatch

```
ApiBlock
├── method: str              # HTTP method: GET | POST | PUT | DELETE
├── url: str                 # Backend URL with ${ENV_VAR} placeholders
│                            # Resolved at load time from os.environ
├── params: dict | None      # For GET: maps tool param names → backend query param names
│                            # e.g., {query: keyword, limit: pageSize}
├── body_mapping: dict | None  # For POST: maps tool param names → backend body fields
│                            # e.g., {customer_id: customerId}
├── item_mapping: dict | None  # For array items in body_mapping
├── response_path: str | None  # Top-level key to extract from backend response
│                            # e.g., "data" → response["data"]
├── response_rename: dict | None  # Rename keys in extracted response
│                            # e.g., {contactNumber: phone, id: customer_id}
└── response_fields: list[str] | None  # Whitelist of fields to return (filter)
```

---

## Entity 3: HandlerRef

**Source**: `handler:` field in YAML tool definition
**Purpose**: Names a Python callable registered in `HANDLER_REGISTRY` at startup

```
HandlerRef
└── name: str    # Must match a key in HANDLER_REGISTRY
                 # e.g., "bi_query_handler"
```

**Handler contract** (all registered handlers MUST conform):
```python
async def handler_name(params: dict, auth: AuthContext) -> dict:
    """
    params: validated execution parameters from the caller
    auth:   current request's AuthContext (token, tenant_id)
    returns: dict of results to return to the calling agent
    raises:  ToolExecutionError on failure
    """
```

---

## Entity 4: AuthContext

**Source**: Extracted from HTTP request headers by `AuthForwardMiddleware`
**Lifetime**: Single HTTP request only (ContextVar — auto-destroyed after response)

```
AuthContext (request-scoped, never persisted)
├── token: str      # Bearer token from Authorization header
│                   # Empty string if header absent
└── tenant_id: str  # Value of X-Tenant-Id header
                    # Empty string if header absent
```

**Invariants**:
- MUST NOT appear in any log output (mask token in structured logs).
- MUST NOT be written to Redis, PostgreSQL, files, or any persistent store.
- MUST NOT be included in any response body.
- Isolation: Each concurrent async request has its own ContextVar copy; no
  cross-request token leakage is possible.

---

## Entity 5: ExecutionRequest

**Source**: Request body of `POST /tools/{name}/execute`

```
ExecutionRequest
└── params: dict    # Key-value pairs matching the tool's declared parameters schema
                    # Validated by FastAPI/Pydantic against the tool's JSON Schema
                    # before dispatch
```

---

## Entity 6: ExecutionResponse

**Source**: Normalized backend response returned by `POST /tools/{name}/execute`

```
ExecutionResponse (success)
├── tool_name: str          # Name of the executed tool
├── result: dict | list     # Normalized result after response_path + response_rename
└── metadata: dict          # {duration_ms: int, backend_status: int}

ExecutionResponse (error)
├── tool_name: str
├── error: str              # English error message
├── error_code: str         # Machine-readable code: TOOL_NOT_FOUND | TIMEOUT |
│                           # BACKEND_ERROR | VALIDATION_ERROR | HANDLER_ERROR
└── metadata: dict          # {duration_ms: int}
```

---

## Entity 7: HealthResponse

**Source**: `GET /health` response

```
HealthResponse
├── status: str          # "healthy" | "degraded"
├── tool_count: int      # Number of currently loaded tools
├── config_path: str     # Absolute path to tools.yaml
└── last_reload: str     # ISO 8601 timestamp of last successful config load
```

---

## Entity 8: ToolStore (Runtime)

**Source**: In-memory singleton, built from YAML parse
**Purpose**: Fast O(1) lookup by tool name; supports namespace filtering

```
ToolStore
├── tools: dict[str, ToolDefinition]   # name → definition
├── by_namespace: dict[str, list[str]] # namespace → [tool names]
├── loaded_at: datetime                # Timestamp of last successful load
└── config_path: str                   # Path to source YAML file
```

**Operations**:
- `get(name) → ToolDefinition | None`
- `list(namespace=None) → list[ToolDefinition]`
- `swap(new_store: ToolStore) → None`  # Atomic replace, thread-safe

---

## Entity 9: OpenAIToolSchema (Wire Format)

**Source**: Output format of `GET /tools` and `ToolRegistryClient.get_openai_tools()`
**Purpose**: Directly consumable by OpenAI GPT-4o/GPT-4o-mini `tools` parameter

```json
{
  "type": "function",
  "function": {
    "name": "<ToolDefinition.name>",
    "description": "<ToolDefinition.description>",
    "parameters": "<ToolDefinition.parameters (JSON Schema object)>"
  }
}
```

---

## YAML Config Schema (Canonical Reference)

```yaml
# config/tools.yaml
tools:
  # HTTP dispatch example
  - name: customer__get_customers      # unique, namespace__verb
    namespace: customer
    description: >
      Find customers by name or phone number.
      Always call this before creating an order to get customer_id.
    parameters:
      type: object
      required: [query]
      properties:
        query:  { type: string,  description: "Name or phone number" }
        limit:  { type: integer, default: 5 }
    api:
      method: GET
      url: "${API_BASE}/customers"
      params:           { query: keyword, limit: pageSize }
      response_path:    data
      response_rename:  { contactNumber: phone, id: customer_id }

  # Handler dispatch example
  - name: bi__run_query
    namespace: bi
    description: >
      Execute a SELECT SQL query on the analytics PostgreSQL database.
      Only SELECT statements allowed; LIMIT 100 applied automatically if absent.
    parameters:
      type: object
      required: [sql]
      properties:
        sql:   { type: string,  description: "SQL SELECT query" }
        limit: { type: integer, default: 100, maximum: 500 }
    handler: bi_query_handler          # dispatches to registered Python callable
```

---

## Environment Variables

| Variable | Used By | Description |
|----------|---------|-------------|
| `API_BASE` | ApiBlock url interpolation | Base URL for REST API |
| `DATABASE_URL` | `bi_query_handler` | asyncpg PostgreSQL connection string |
| `TOOL_REGISTRY_PORT` | Docker Compose / main.py | Service port (default: 8001) |
| `TOOLS_YAML_PATH` | config_loader | Path to tools.yaml (default: `config/tools.yaml`) |
| `HOTRELOAD_INTERVAL_S` | config_loader | Watchdog debounce interval (default: 1) |

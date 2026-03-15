# Quickstart: Tool Registry v1

**Branch**: `001-tool-registry` | **Service port**: 8001

---

## Prerequisites

- Python 3.12
- Docker Compose (or run locally with `uvicorn`)
- `KIOTVIET_API_BASE` environment variable set (for customer/order tools)
- `DATABASE_URL` environment variable set (for `bi_query_handler`)

---

## 1. Define Tools in YAML

Edit `config/tools.yaml`. Each tool entry needs a unique `name`, a `namespace`, a
`description`, a `parameters` JSON Schema, and exactly one of `api` or `handler`:

```yaml
tools:
  # HTTP-dispatch tool (calls an external REST API)
  - name: customer__get_customers
    namespace: customer
    description: >
      Find customers by name or phone number.
      Always call this before creating an order to get customer_id.
    parameters:
      type: object
      required: [query]
      properties:
        query: { type: string, description: "Name or phone number" }
        limit: { type: integer, default: 5 }
    api:
      method: GET
      url: "${KIOTVIET_API_BASE}/customers"
      params: { query: keyword, limit: pageSize }
      response_path: data
      response_rename: { contactNumber: phone, id: customer_id }

  # Handler-dispatch tool (calls a Python function directly)
  - name: bi__run_query
    namespace: bi
    description: >
      Execute a SELECT SQL query on the analytics PostgreSQL database.
      Only SELECT statements; LIMIT 100 applied automatically if absent.
    parameters:
      type: object
      required: [sql]
      properties:
        sql:   { type: string,  description: "SQL SELECT query" }
        limit: { type: integer, default: 100, maximum: 500 }
    handler: bi_query_handler
```

---

## 2. Run via Docker Compose

```bash
# From repo root
docker compose up tool-registry

# Verify health
curl http://localhost:8001/health
# → {"status": "healthy", "tool_count": 5, ...}
```

---

## 3. Run Locally (Development)

```bash
cd tool_registry
KIOTVIET_API_BASE=https://api.kiotviet.vn/v3 \
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/analytics \
uvicorn main:app --reload --port 8001
```

---

## 4. Discover Tools

```bash
# All tools
curl http://localhost:8001/tools

# Filter by namespace
curl "http://localhost:8001/tools?namespace=customer"
```

Response (OpenAI function-call format):
```json
[
  {
    "type": "function",
    "function": {
      "name": "customer__get_customers",
      "description": "Find customers by name or phone number...",
      "parameters": {
        "type": "object",
        "required": ["query"],
        "properties": {
          "query": { "type": "string", "description": "Name or phone number" },
          "limit": { "type": "integer", "default": 5 }
        }
      }
    }
  }
]
```

---

## 5. Execute a Tool

```bash
# With Bearer token (forwarded to backend, never stored)
curl -X POST http://localhost:8001/tools/customer__get_customers/execute \
  -H "Authorization: Bearer <your-token>" \
  -H "X-Tenant-Id: tenant-001" \
  -H "Content-Type: application/json" \
  -d '{"query": "Nguyen Van A", "limit": 3}'
```

Response:
```json
{
  "tool_name": "customer__get_customers",
  "result": [
    { "customer_id": "12345", "name": "Nguyen Van A", "phone": "0901234567" }
  ],
  "metadata": { "duration_ms": 312, "backend_status": 200 }
}
```

---

## 6. Use ToolRegistryClient (in Agent Code)

```python
from shared.tool_registry_client import ToolRegistryClient
from shared.auth_context import set_auth

# Set auth context before calling (typically done by middleware in agent service)
set_auth(token="Bearer-token-here", tenant_id="tenant-001")

client = ToolRegistryClient(base_url="http://tool-registry:8001")

# Discover tools for LLM
tools = await client.get_openai_tools(namespace="customer")
# → list of OpenAI function-call dicts, ready for GPT-4o tools= parameter

# Execute a tool
result = await client.execute(
    name="customer__get_customers",
    params={"query": "Lam", "limit": 5}
)
# → {"customer_id": "123", "name": "Lam", "phone": "0901234567"}
```

---

## 7. Hot-Reload Config

The service watches `config/tools.yaml` for changes. Edit the file while the service
is running:

```bash
# Add a new tool to config/tools.yaml, save the file
# Within ~1-5 seconds:
curl http://localhost:8001/health
# → tool_count incremented, last_reload timestamp updated
```

No service restart required.

---

## 8. Run Tests

```bash
# All tests for this feature
pytest tests/001-tool-registry/ -v

# Contract tests only (run first, before implementation)
pytest tests/001-tool-registry/contract/ -v

# Integration tests (requires running service or test fixtures)
pytest tests/001-tool-registry/integration/ -v

# Unit tests only
pytest tests/001-tool-registry/unit/ -v
```

---

## Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `KIOTVIET_API_BASE` | Yes (for HTTP tools) | — | KiotViet API base URL |
| `DATABASE_URL` | Yes (for bi handler) | — | asyncpg PostgreSQL connection string |
| `TOOL_REGISTRY_PORT` | No | `8001` | Service listen port |
| `TOOLS_YAML_PATH` | No | `config/tools.yaml` | Path to tool definitions |
| `HOTRELOAD_INTERVAL_S` | No | `1` | Watchdog debounce interval (seconds) |

---

## Common Errors

| Error code | Cause | Fix |
|------------|-------|-----|
| `TOOL_NOT_FOUND` | Tool name not in registry | Check spelling; verify YAML loaded via `/health` |
| `VALIDATION_ERROR` | Params don't match tool schema | Check required fields and types against `GET /tools` |
| `TIMEOUT` | Backend took > 10 s | Check backend health; increase timeout via config if needed |
| `BACKEND_ERROR` | Backend returned non-2xx | Check `X-Tenant-Id` header; verify backend credentials |
| `HANDLER_ERROR` | Python handler raised exception | Check `DATABASE_URL`; inspect service logs |

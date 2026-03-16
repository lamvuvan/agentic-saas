# Quickstart: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Ports**: Orchestrator:8000, Order Agent:8002, BI Agent:8003

---

## Prerequisites

- Docker Compose with all 6 services: orchestrator, tool-registry, order-agent, bi-agent, redis, postgres
- `.env` with: `OPENAI_API_KEY`, `API_BASE`, `DATABASE_URL`, `TOOL_REGISTRY_URL=http://tool-registry:8001`
- Tool Registry (feature 001) deployed and healthy

---

## 1. Start All Services

```bash
docker compose up
# Wait for all 6 services to report healthy
curl http://localhost:8000/health   # Orchestrator
curl http://localhost:8002/health   # Order Agent
curl http://localhost:8003/health   # BI Agent
```

---

## 2. Verify Agent Cards

```bash
# Orchestrator Agent Card
curl http://localhost:8000/.well-known/agent.json

# Order Agent Card
curl http://localhost:8002/.well-known/agent.json

# BI Agent Card
curl http://localhost:8003/.well-known/agent.json
```

Expected: JSON with `role`, `skills`, and `slo` fields.

---

## 3. Intent Classification (US1)

Send a message and observe intent routing:

```bash
# Order intent
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "anh Lâm hai trứng lộn một cháo lòng"}'

# Expected: intent="order", requires_input=true (awaiting confirmation)

# BI intent
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "doanh thu hôm nay bao nhiêu"}'

# Expected: intent="bi_query", reply contains revenue figure

# Chitchat
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "xin chào"}'

# Expected: intent="chitchat", polite reply
```

---

## 4. Complete Order Flow (US2 — Multi-Turn)

```bash
# Step 1: Start order
RESPONSE=$(curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "bàn 3 cho tôi 3 bò kho bánh mì"}')

SESSION_ID=$(echo $RESPONSE | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session: $SESSION_ID"
# Reply should show order preview with table=3, items=[bò kho×3, bánh mì×3], requires_input=true

# Step 2: Confirm the order
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d "{\"message\": \"xác nhận\", \"session_id\": \"$SESSION_ID\"}"

# Reply should confirm order with order_code
```

---

## 5. BI Query (US3)

```bash
# Revenue query
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "top 5 khách hàng mua nhiều nhất tháng này"}'

# Debt query
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"message": "danh sách khách hàng còn công nợ trên 1 triệu"}'

# Expected: Formatted list response with data
```

---

## 6. Voice Input (US6)

```bash
# Record a voice clip (or use a test clip)
curl -X POST http://localhost:8000/voice \
  -H "Authorization: Bearer your-token" \
  -F "audio=@/path/to/clip.wav" \
  -F "session_id=optional-session-id"

# Response includes: transcription + reply (same as /chat)
```

---

## 7. Direct A2A Task (US5)

Test the A2A protocol directly:

```bash
# Submit task to Order Agent
TASK=$(curl -s -X POST http://localhost:8002/a2a/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"skill": "create_order", "params": {"message": "anh Lâm hai trứng lộn", "session_id": "test-session-1"}}')

TASK_ID=$(echo $TASK | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")
echo "Task ID: $TASK_ID"

# Poll for result
sleep 2
curl http://localhost:8002/a2a/tasks/$TASK_ID

# Expected: status=input-required (awaiting confirmation) or completed
```

---

## 8. Run Eval Suite

```bash
# Intent classification eval (target: ≥ 90%)
python3 evals/runner.py evals/cases/intent_classification.yaml

# Entity extraction eval (target: ≥ 90%)
python3 evals/runner.py evals/cases/entity_extraction.yaml

# NL2SQL eval (target: ≥ 80%)
python3 evals/runner.py evals/cases/nl2sql.yaml

# Full e2e order eval (target: ≥ 85%)
python3 evals/runner.py evals/cases/e2e_order.yaml

# Or all at once
make eval-all
```

---

## 9. Run Tests

```bash
# Contract tests first (TDD — must run before implementation)
pytest tests/002-orchestrator-domain-agents/contract/ -v

# Unit tests
pytest tests/002-orchestrator-domain-agents/unit/ -v

# Integration tests (requires Redis + Tool Registry running)
pytest tests/002-orchestrator-domain-agents/integration/ -v
```

---

## Key Environment Variables

| Variable | Service | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | all | OpenAI API key |
| `TOOL_REGISTRY_URL` | orchestrator, agents | Tool Registry base URL |
| `REDIS_URL` | all | Redis connection string |
| `DATABASE_URL` | bi-agent | PostgreSQL analytics DB |
| `API_BASE` | tool-registry | API base URL |
| `ORDER_AGENT_URL` | orchestrator | Order Agent A2A URL |
| `BI_AGENT_URL` | orchestrator | BI Agent A2A URL |
| `OPENAI_MODEL_FAST` | all | Fast model name (default: gpt-4o-mini) |
| `OPENAI_MODEL_SMART` | all | Smart model name (default: gpt-4o) |
| `A2A_POLL_INTERVAL_MS` | orchestrator | A2A polling interval (default: 500) |
| `A2A_TIMEOUT_MS` | orchestrator | A2A max wait time (default: 30000) |

# Quick Start

## Yêu cầu

- Docker & Docker Compose
- Python 3.12+ (để chạy local hoặc tests)
- OpenAI API key
- KiotViet API access

---

## 1. Cấu hình môi trường

```bash
cp .env.example .env
```

Chỉnh sửa `.env`, điền các giá trị bắt buộc:

```bash
OPENAI_API_KEY=sk-...
KIOTVIET_API_BASE=https://api.kiotviet.vn/v3
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/analytics
```

---

## 2. Chạy toàn bộ stack (Docker)

```bash
make dev
```

Hoặc không có Makefile:

```bash
docker compose up --build
```

Các service sẽ khởi động theo thứ tự: Redis → PostgreSQL → Tool Registry → Order Agent → BI Agent → Orchestrator.

Kiểm tra health:

```bash
curl http://localhost:8000/health   # Orchestrator
curl http://localhost:8001/health   # Tool Registry
curl http://localhost:8002/health   # Order Agent
curl http://localhost:8003/health   # BI Agent
```

---

## 3. Chạy từng service local (development)

**Tool Registry:**
```bash
TOOLS_YAML_PATH=config/tools.yaml uvicorn tool_registry.main:create_app --factory --port 8001 --reload
```

**Orchestrator:**
```bash
REDIS_URL=redis://localhost:6379/0 uvicorn orchestrator.main:app --port 8000 --reload
```

**Order Agent:**
```bash
REDIS_URL=redis://localhost:6379/0 TOOL_REGISTRY_URL=http://localhost:8001 \
  uvicorn order_agent.main:app --port 8002 --reload
```

**BI Agent:**
```bash
REDIS_URL=redis://localhost:6379/0 TOOL_REGISTRY_URL=http://localhost:8001 \
  uvicorn bi_agent.main:app --port 8003 --reload
```

---

## 4. Gọi API

### Chat (text)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"message": "cho tôi xem doanh thu tháng này"}'
```

Response:
```json
{
  "session_id": "uuid",
  "reply": "Doanh thu tháng này là...",
  "intent": "bi_query",
  "trace_id": "uuid",
  "requires_input": false,
  "metadata": {"duration_ms": 1234}
}
```

### Voice (audio)

```bash
curl -X POST http://localhost:8000/voice \
  -H "Authorization: Bearer <token>" \
  -F "audio=@recording.wav"
```

### Agent cards

```bash
curl http://localhost:8000/.well-known/agent.json
curl http://localhost:8002/.well-known/agent.json
curl http://localhost:8003/.well-known/agent.json
```

---

## 5. Cài đặt dependencies (local dev)

```bash
pip install -r requirements.txt
# Hoặc với dev tools:
pip install -r requirements-dev.txt
```

---

## 6. Chạy tests

```bash
# Contract tests (không cần services running)
pytest tests/002-orchestrator-domain-agents/contract/ -v

# Unit tests
pytest tests/002-orchestrator-domain-agents/unit/ -v

# Integration tests (cần Redis)
pytest tests/002-orchestrator-domain-agents/integration/ -v

# Tool Registry (tất cả)
pytest tests/001-tool-registry/ -v
```

---

## 7. Evals

```bash
# Chạy tất cả eval suites
python -m evals.runner --all

# Chạy một suite cụ thể
python -m evals.runner --suite intent_classification --base-url http://localhost:8000
python -m evals.runner --suite nl2sql --base-url http://localhost:8003
```

---

## 8. Lint & Format

```bash
ruff check .        # Kiểm tra lỗi
ruff format .       # Auto-format
ruff check --fix .  # Auto-fix
```

---

## Luồng đặt hàng (multi-turn)

```
User: "cho tôi 2 ly cà phê đen bàn 3"
  → Orchestrator phân loại: intent=order_create
  → Order Agent: extract entities → match sản phẩm FAISS
  → Trả về preview + yêu cầu xác nhận

User: "đúng rồi, xác nhận"
  → Orchestrator gửi continuation task_id
  → Order Agent resume: submit order → Tool Registry → KiotViet
  → Trả về order_code
```

## Luồng BI query

```
User: "doanh thu hôm nay so với hôm qua"
  → Orchestrator phân loại: intent=bi_query
  → BI Agent: NL→SQL (GPT-4o) → safety check → inject LIMIT → execute → format
  → Trả về kết quả bằng tiếng Việt
```

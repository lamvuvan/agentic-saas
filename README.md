# agentic-saas

Hệ thống multi-agent AI cho vận hành thương mại điện tử tiếng Việt — nhận lệnh thoại/text, phân loại ý định, điều phối đến các domain agent chuyên biệt để xử lý đơn hàng và truy vấn dữ liệu kinh doanh.

## Kiến trúc

```
                        ┌──────────────────────────────┐
                        │         Orchestrator          │
   chat / voice  ──────►│  :8000  classify → plan       │
                        │         dispatch → aggregate  │
                        └──────────┬───────────┬────────┘
                                   │ A2A       │ A2A
                    ┌──────────────┘           └──────────────┐
                    ▼                                          ▼
          ┌─────────────────┐                      ┌──────────────────┐
          │   Order Agent   │                      │    BI Agent      │
          │  :8002          │                      │  :8003           │
          │  extract →      │                      │  nl2sql →        │
          │  match →        │                      │  safety_check →  │
          │  preview →      │                      │  execute →       │
          │  confirm →      │                      │  format          │
          │  submit         │                      └──────────────────┘
          └────────┬────────┘                                 │
                   │                                          │
                   └──────────────┬───────────────────────────┘
                                  ▼
                       ┌──────────────────┐
                       │  Tool Registry   │
                       │  :8001           │
                       │  YAML hot-reload │
                       └──────────────────┘
```

### Services

| Service | Port | Mô tả |
|---|---|---|
| Orchestrator | 8000 | Entry point chat/voice, phân loại ý định, lập kế hoạch, tổng hợp kết quả |
| Order Agent | 8002 | Xử lý đơn hàng: trích xuất thực thể, khớp sản phẩm (FAISS), xác nhận, gửi đơn |
| BI Agent | 8003 | Truy vấn phân tích: NL→SQL, kiểm tra an toàn, thực thi, format kết quả |
| Tool Registry | 8001 | Registry tập trung cho tất cả tools/capabilities (YAML config, hot-reload <5s) |
| Redis | 6379 | Session state, LangGraph checkpointer, A2A task store |
| PostgreSQL | 5432 | Analytics DB với pgvector extension |

## Tech Stack

- **Runtime:** Python 3.12, FastAPI 0.111+, Pydantic v2, uvicorn
- **AI/LLM:** LangGraph 0.2+, LangChain, OpenAI (GPT-4o / GPT-4o-mini)
- **Speech:** faster-whisper large-v3 (int8, VAD filter)
- **Vector Search:** sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2`, FAISS IndexFlatIP
- **Async I/O:** httpx, asyncpg
- **Testing:** pytest, pytest-asyncio, respx
- **Linting:** ruff

## Cấu trúc thư mục

```
orchestrator/           # Service port 8000 — graph pipeline (classify→plan→dispatch→aggregate)
order_agent/            # Service port 8002 — order fulfillment graph
bi_agent/               # Service port 8003 — NL2SQL + query execution graph
tool_registry/          # Service port 8001 — tool registry, HTTP + handler dispatch
shared/                 # Shared libs: auth_context, tool_registry_client, a2a/, llm_client
config/                 # tools.yaml, bi_schema.yaml
tests/                  # contract/ integration/ unit/ cho từng service
specs/                  # Feature specs, data models, API contracts
evals/                  # Evaluation runner và test cases
```

## Các nguyên tắc thiết kế

- **A2A Protocol** — Agent giao tiếp qua HTTP polling: `POST /a2a/tasks` (202) → `GET /a2a/tasks/{id}`
- **Tool Registry First** — Mọi capability phải được đăng ký trước khi dùng
- **Bearer Token via ContextVar** — Token không bao giờ được log hoặc lưu vào task record
- **Test-First** — Contract tests viết trước implementation
- **Atomic FAISS Refresh** — Build index mới rồi swap dưới lock, không update partial

## Môi trường

Xem [.env.example](.env.example) để biết danh sách đầy đủ biến môi trường.

Các biến bắt buộc:
```
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/analytics
KIOTVIET_API_BASE=https://api.kiotviet.vn/v3
```

## Chạy nhanh

Xem [QUICKSTART.md](QUICKSTART.md).

## Tests

```bash
# Contract tests (chạy trước)
pytest tests/002-orchestrator-domain-agents/contract/

# Tất cả tests feature 002
pytest tests/002-orchestrator-domain-agents/

# Tool Registry tests
pytest tests/001-tool-registry/

# Lint
ruff check . && ruff format .
```

## Evals

```bash
python -m evals.runner --all
python -m evals.runner --suite intent_classification --base-url http://localhost:8000
```

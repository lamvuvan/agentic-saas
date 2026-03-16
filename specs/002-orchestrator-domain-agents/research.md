# Research: Orchestrator and Domain Agents

**Branch**: `002-orchestrator-domain-agents` | **Date**: 2026-03-16
**Sources**: Agentic_WorkPlan_v2.md, LangGraph docs, Google A2A spec, sentence-transformers docs, faster-whisper README

---

## Decision 1: LangGraph Redis Checkpointer for Agent State

**Decision**: Use `langgraph-checkpoint-redis` with `AsyncRedisSaver` as the LangGraph checkpointer. Key the checkpoint by `thread_id = session_id` (Orchestrator) or `thread_id = a2a_task_id` (Domain Agents for multi-turn).

**Rationale**:
- LangGraph natively supports Redis checkpointers via `langgraph-checkpoint-redis`
- `AsyncRedisSaver` integrates with FastAPI's async event loop without blocking
- Keying by `thread_id` allows resuming interrupted graphs (e.g., Order Agent confirmation interrupt) from any service instance — stateless HTTP handlers
- Redis already deployed in the stack (session state, short-term memory per work plan)
- Plan serialization is automatic: LangGraph checkpoints include the full graph state including the `ExecutionPlan` object, satisfying Constitution Principle V

**Alternatives considered**:
- PostgreSQL checkpointer (`langgraph-checkpoint-postgres`) — higher latency, overkill for short-lived session state; Redis is sufficient for ≤ 30 minute sessions
- In-memory checkpointer — violates Constitution V (not re-entrant across restarts); rejected
- Custom Redis state management — more code, same outcome; LangGraph's built-in is simpler

**Key constraints**:
- `AsyncRedisSaver` must be initialized with a `redis.asyncio` connection pool (`redis-py>=4.6`); do NOT use legacy `aioredis`
- Thread ID (session ID) must be scoped per user/session — never shared across users
- LangGraph resume: pass `None` as input with the same `thread_id`: `await graph.ainvoke(None, {"configurable": {"thread_id": tid}})` — `None` signals "resume from checkpoint"
- The A2A `input-required` status stores the `thread_id` in the task hash so any worker pod can resume the graph
- Pin versions together: `langgraph==0.2.x` + `langgraph-checkpoint-redis>=0.0.6`; set a Redis TTL on checkpoint keys (recommended: 24 hours)

---

## Decision 2: A2A Protocol — HTTP Polling with Redis Task Store

**Decision**: Implement A2A as lightweight HTTP polling: `POST /a2a/tasks` returns a task ID immediately; `GET /a2a/tasks/{id}` returns status + result. Task state stored in Redis hash with 1-hour TTL. Orchestrator polls at 500ms intervals with 30s total timeout.

**Rationale**:
- The work plan specifies `POST /a2a/tasks` + `GET /a2a/tasks/{id}` polling explicitly
- HTTP polling is simpler than SSE streaming for the MVP timeframe
- 30s timeout with 500ms polling = max 60 poll attempts — well within Redis capacity
- Redis task store is stateless: any Orchestrator pod can poll any task from any Domain Agent pod
- P95 latency targets (3s order, 5s BI) are achievable: polling overhead is ≤ 500ms average wait

**Alternatives considered**:
- SSE (Server-Sent Events) per A2A spec — more complex client implementation, not needed at < 10 concurrent users; deferred to v1.1
- WebSocket streaming — highest complexity; YAGNI for MVP
- Direct synchronous RPC — violates async A2A design; Order Agent's confirmation interrupt requires multi-turn state which cannot be handled synchronously

**Key constraints**:
- Task ID must be returned immediately (< 200ms) regardless of task complexity
- `status: "input-required"` signals that Domain Agent is awaiting user input; Orchestrator forwards the prompt back to the user and re-submits with user input as continuation
- Redis hash key: `a2a:task:{task_id}` with fields: `status`, `skill`, `params`, `result`, `error`, `created_at`, `updated_at`
- Task TTL: 1 hour for completed/failed; no TTL for `working`/`input-required` tasks (cleaned up on completion)

**Agent Card schema** (`/.well-known/agent.json`):
```json
{
  "name": "Order Agent",
  "version": "1.0.0",
  "role": "domain",
  "domain": "order",
  "url": "http://order-agent:8002",
  "skills": [
    {
      "id": "create_order",
      "description": "Create a customer order from Vietnamese natural language input",
      "inputModes": ["text"],
      "outputModes": ["text"]
    }
  ],
  "securitySchemes": {
    "bearer": { "type": "http", "scheme": "bearer" }
  }
}
```

---

## Decision 3: FAISS + paraphrase-multilingual-MiniLM-L12-v2 for Product Matching

**Decision**: Use FAISS `IndexFlatIP` (inner product = cosine with normalized vectors) with `paraphrase-multilingual-MiniLM-L12-v2` sentence embeddings. Similarity thresholds: ≥ 0.85 auto-select, 0.65–0.84 present top-3 for GPT-4o-mini rerank, < 0.65 ask user to re-describe.

**Rationale**:
- `paraphrase-multilingual-MiniLM-L12-v2` supports 50+ languages including Vietnamese; recall@3 ≥ 90% for product name variations per work plan benchmark target
- FAISS `IndexFlatIP` with L2-normalized vectors = cosine similarity; fast for ≤ 10k products (< 5ms query)
- Vietnamese text normalization before embedding: unicode NFC, lowercase, honorific strip — embeddings handle remaining variation
- In-memory FAISS avoids network latency vs pgvector; entire product catalog fits in < 100MB RAM

**Alternatives considered**:
- pgvector extension (PostgreSQL) — already in stack, but adds network hop for each product lookup; FAISS is ≤ 1ms vs 5-20ms for network call
- BM25 (sparse retrieval) — works for exact/near-exact name matches but fails for Vietnamese variations (abbreviations, diacritics, local names); dense embeddings are more robust
- Larger model (multilingual-e5-large) — higher accuracy but 10× slower inference; MiniLM achieves ≥ 90% recall@3 which is sufficient

**Vietnamese text preprocessing pipeline**:
```
raw_text
  → unicode NFC normalization
  → lowercase
  → remove leading/trailing whitespace
  → honorific strip (anh/chị/em/bác/cô/chú/ông/bà → remove)
  → number word conversion (một→1, hai→2, ba→3, bốn→4, năm→5, sáu→6, bảy→7, tám→8, chín→9, mười→10)
  → embed with MiniLM
```

**Key constraints**:
- Index built at startup from product catalog API; rebuild every 30 minutes in background `asyncio.create_task`
- Rebuild must be atomic: build new index into temp object, then swap reference — never partially update live index
- Product ID (KiotViet `id`) stored in parallel list aligned with FAISS index positions

---

## Decision 4: faster-whisper for Vietnamese STT

**Decision**: Use `faster-whisper` with `large-v3` model, `compute_type="int8"` for CPU deployment. Audio normalized to 16kHz mono WAV before transcription. Post-process output to strip leading/trailing whitespace and join segments.

**Rationale**:
- `faster-whisper` is 4× faster than original OpenAI Whisper with equivalent accuracy
- `large-v3` achieves ~10-12% WER on clear Vietnamese speech (< 15% target from work plan)
- `compute_type="int8_float16"` on GPU (activations float16, weights int8) or `"int8"` on CPU-only — ~4× speedup vs float32 with < 2% WER increase
- Vietnamese requires no special language forcing; `language="vi"` passed to `transcribe()`

**Alternatives considered**:
- OpenAI Whisper API — cloud dependency, latency variability, per-minute cost; local is more predictable and private
- Smaller model (medium) — WER ~18-20% on Vietnamese; below the 15% target
- Real-time streaming (WebSocket) — out of scope for v1 per spec; file upload only in MVP

**Key constraints**:
- Audio preprocessing: resample to 16kHz with `librosa` or `ffmpeg` subprocess; accept WAV/MP3/M4A/OGG
- Model loaded once at startup in lifespan; inference is CPU-bound so runs in `asyncio.run_in_executor(None, transcribe_fn)`
- Enable `vad_filter=True` in `transcribe()` to skip silence segments — significantly reduces latency for audio with pauses
- Cache model to a persistent Docker volume via `download_root` parameter — do NOT re-download on container start
- POST /voice endpoint: multipart form upload, returns `{text: "transcribed text", wer_estimate: null}`
- Max audio duration: 60 seconds in MVP (longer clips rejected with 422)

---

## Decision 5: OpenAI Model Routing Strategy

**Decision**: Route by task type using a `select_model(task_type: str) -> str` function. Default GPT-4o-mini for structured/fast tasks; escalate to GPT-4o when: (a) task type explicitly requires it, or (b) confidence < 0.72 from GPT-4o-mini output.

**Rationale**:
- GPT-4o-mini: ~20× cheaper than GPT-4o, < 1s latency, sufficient for intent classification and entity extraction with good few-shot examples
- GPT-4o: Required for complex multi-step reasoning (Orchestrator planning, NL2SQL with complex joins)
- Confidence-based escalation prevents silent failures without always paying for GPT-4o

**Model routing table**:

| Task Type | Default Model | Escalation Trigger |
|-----------|-------------|-------------------|
| `intent_classify` | gpt-4o-mini | confidence < 0.72 → gpt-4o |
| `entity_extract` | gpt-4o-mini | confidence < 0.72 → gpt-4o |
| `orchestrator_plan` | gpt-4o | always gpt-4o; o1-mini for > 3-agent plans (out of scope v1) |
| `nl2sql` | gpt-4o | always gpt-4o; complex joins require reasoning |
| `response_format` | gpt-4o-mini | never escalates |
| `product_rerank` | gpt-4o-mini | never escalates |

**Alternatives considered**:
- Always GPT-4o — 20× cost increase; P95 latency exceeds 3s target for order conversations
- Always GPT-4o-mini — NL2SQL accuracy drops to ~65%; below 80% target
- Fine-tuned GPT-4o-mini — best accuracy (~95%+) but requires ≥50 labeled examples per intent and a fine-tuning pipeline; deferred to v2

**Key constraints**:
- `shared/llm_client.py` implements `select_model()`, `chat_completion_async()`, retry logic (3 attempts, exponential backoff), and token tracking
- Use `response_format={"type": "json_schema", "json_schema": {...}}` (Structured Outputs GA) for field-level schema enforcement — `json_object` mode guarantees valid JSON but NOT schema conformance
- Always validate LLM JSON output with Pydantic before trusting it (models occasionally output wrong types despite schema instructions)
- All prompts use system + few-shot user/assistant pairs; `temperature=0` for determinism
- Keep system prompt + few-shots under 800 tokens for < 1s TTFT at P95 on GPT-4o-mini

---

## Decision 6: Orchestrator Plan Schema

**Decision**: `ExecutionPlan` is a Pydantic v2 model serialized to JSON for Redis persistence. Includes `goal`, `steps` (ordered list), `agent_assignments`, `dependencies`, and `success_criteria`. Persisted immediately after generation (Constitution V).

**Plan schema**:
```json
{
  "plan_id": "uuid4",
  "goal": "Create order for customer Lâm",
  "intent": "order",
  "steps": [
    {
      "step_id": "step-1",
      "agent": "order-agent",
      "skill": "create_order",
      "params": {"message": "anh Lâm hai trứng lộn", "session_id": "sess-abc"},
      "depends_on": [],
      "status": "pending"
    }
  ],
  "created_at": "2026-03-16T08:00:00Z",
  "replan_count": 0,
  "max_replans": 3
}
```

**Rationale**: Pydantic v2 `.model_dump_json()` gives deterministic JSON. Redis `SET plan:{plan_id}` with EX=3600 (1 hour TTL). Plan stored immediately in `plan_execution` node before any A2A dispatch.

---

## Decision 7: Vietnamese NLP Utilities

**Decision**: Implement `vn_utils.py` with `normalize_text()`, `strip_honorifics()`, `words_to_numbers()`, and `extract_product_note()` pure functions. All tested with 50 unit test cases.

**Number word mapping** (from work plan):
```python
VN_NUMBERS = {
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
    "mười một": 11, "mười hai": 12
}
HONORIFICS = {"anh", "chị", "em", "bác", "cô", "chú", "ông", "bà"}
```

**Rationale**: Pure functions are easily unit-tested and do not depend on LLM calls. These transformations boost entity extraction accuracy significantly for common Vietnamese patterns.

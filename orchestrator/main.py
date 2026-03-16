"""Orchestrator FastAPI application — port 8000."""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.a2a.models import AgentCard, AgentSkill
from shared.auth_context import set_auth
from shared.logging_middleware import StructuredLoggingMiddleware

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_redis_pool: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_pool
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _redis_pool = aioredis.from_url(redis_url, decode_responses=True)
    app.state.redis = _redis_pool
    logger.info("orchestrator_startup", extra={"redis_url": redis_url.split("@")[-1]})

    # Initialize AsyncRedisSaver checkpointer (thread_id=session_id)
    from orchestrator.graph import setup_checkpointer  # noqa: PLC0415

    await setup_checkpointer(redis_url)

    yield
    await _redis_pool.aclose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Orchestrator", version="1.0.0", lifespan=lifespan)
app.add_middleware(StructuredLoggingMiddleware, agent_id="orchestrator", agent_role="orchestrator")

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------

_AGENT_CARD = AgentCard(
    name="Orchestrator",
    version="1.0.0",
    role="orchestrator",
    url=os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8000"),
    description="Classifies intent, plans execution, delegates to Domain Agents via A2A",
    skills=[
        AgentSkill(
            id="chat",
            description="Process natural language messages and route to Domain Agents",
            input_modes=["text"],
            output_modes=["text"],
        )
    ],
    slo={"p95_latency_ms": 3000, "availability_pct": 99.0},
)


@app.get("/.well-known/agent.json", tags=["meta"])
async def agent_card() -> dict[str, Any]:
    return _AGENT_CARD.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health(request: Request) -> dict[str, Any]:
    redis: aioredis.Redis = request.app.state.redis
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    status = "healthy" if redis_ok else "degraded"
    code = 200 if redis_ok else 503
    return JSONResponse(
        {"status": status, "dependencies": {"redis": "ok" if redis_ok else "error"}},
        status_code=code,
    )


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    intent: str
    trace_id: str
    requires_input: bool = False
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    start = time.monotonic()

    # Auth context from Authorization header
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    tenant_id = request.headers.get("X-Tenant-Id", "default")
    set_auth(token, tenant_id)

    session_id = body.session_id or request.headers.get("X-Session-Id") or str(uuid.uuid4())
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())

    from orchestrator.graph import invoke_chat  # noqa: PLC0415

    result = await invoke_chat(
        message=body.message,
        session_id=session_id,
        trace_id=trace_id,
        redis=request.app.state.redis,
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    return ChatResponse(
        session_id=session_id,
        reply=result["reply"],
        intent=result["intent"],
        trace_id=trace_id,
        requires_input=result.get("requires_input", False),
        metadata={"duration_ms": duration_ms, "model_used": result.get("model_used")},
    )

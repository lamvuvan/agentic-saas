"""BI Agent FastAPI application — port 8003."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared.a2a.models import AgentCard, AgentSkill
from shared.logging_middleware import StructuredLoggingMiddleware

logger = logging.getLogger(__name__)

_redis_pool: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _redis_pool
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    _redis_pool = aioredis.from_url(redis_url, decode_responses=True)
    app.state.redis = _redis_pool

    logger.info("bi_agent_startup", extra={"redis_url": redis_url.split("@")[-1]})
    yield
    await _redis_pool.aclose()


app = FastAPI(title="BI Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(StructuredLoggingMiddleware, agent_id="bi-agent", agent_role="domain")

_AGENT_CARD = AgentCard(
    name="BI Agent",
    version="1.0.0",
    role="domain",
    domain="bi",
    url=os.environ.get("BI_AGENT_URL", "http://bi-agent:8003"),
    description="Answers business intelligence questions by translating Vietnamese to SQL and formatting results",
    skills=[
        AgentSkill(
            id="bi_query",
            description="Translate Vietnamese question to SQL, execute on analytics DB, format human-readable answer",
            input_modes=["text"],
            output_modes=["text"],
        )
    ],
    slo={"p95_latency_ms": 5000, "availability_pct": 99.0},
)


@app.get("/.well-known/agent.json", tags=["meta"])
async def agent_card() -> dict[str, Any]:
    return _AGENT_CARD.model_dump(exclude_none=True)


@app.get("/health", tags=["meta"])
async def health(request: Request) -> dict[str, Any]:
    redis: aioredis.Redis = request.app.state.redis
    try:
        await redis.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    status = "healthy" if redis_ok else "degraded"
    return JSONResponse(
        {"status": status, "dependencies": {"redis": "ok" if redis_ok else "error"}},
        status_code=200 if redis_ok else 503,
    )


# A2A router mounted in T019
from bi_agent.a2a_server import router as a2a_router  # noqa: E402

app.include_router(a2a_router)

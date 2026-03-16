"""Order Agent FastAPI application — port 8002."""

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

    # ProductMatcher index built in T038 (US2)
    # from order_agent.product_matcher import ProductMatcher
    # app.state.product_matcher = ProductMatcher()
    # await app.state.product_matcher.initialize()

    logger.info("order_agent_startup", extra={"redis_url": redis_url.split("@")[-1]})

    # Initialize AsyncRedisSaver checkpointer (thread_id=task_id)
    from order_agent.graph import setup_checkpointer  # noqa: PLC0415

    await setup_checkpointer(redis_url)

    yield
    await _redis_pool.aclose()


app = FastAPI(title="Order Agent", version="1.0.0", lifespan=lifespan)
app.add_middleware(StructuredLoggingMiddleware, agent_id="order-agent", agent_role="domain")

_AGENT_CARD = AgentCard(
    name="Order Agent",
    version="1.0.0",
    role="domain",
    domain="order",
    url=os.environ.get("ORDER_AGENT_URL", "http://order-agent:8002"),
    description="Creates customer orders from Vietnamese natural language input via ReAct reasoning loop",
    skills=[
        AgentSkill(
            id="create_order",
            description="Extract entities, match products, confirm with user, submit order",
            input_modes=["text"],
            output_modes=["text"],
        )
    ],
    slo={"p95_latency_ms": 3000, "availability_pct": 99.0},
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
from order_agent.a2a_server import router as a2a_router  # noqa: E402

app.include_router(a2a_router)

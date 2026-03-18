"""Orchestrator FastAPI application — port 8000."""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from shared.a2a.models import AgentCard, AgentSkill
from shared.auth_context import set_auth
from shared.logging_middleware import StructuredLoggingMiddleware

logger = logging.getLogger(__name__)

_MIGRATION_PATH = Path(__file__).parent / "migrations" / "001_plans.sql"
_MIGRATION_003_PATH = Path(__file__).parent / "migrations" / "003_orchestrator_memory.sql"

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

    # Initialize AgentRegistry — fetch Domain Agent cards at startup
    from orchestrator.agent_registry import AgentRegistry  # noqa: PLC0415

    registry = AgentRegistry.from_env()
    await registry.start()
    app.state.registry = registry

    # Initialize PostgreSQL pool for Plan Visibility + Orchestrator Memory (optional — graceful degradation)
    app.state.db_pool = None
    app.state.plan_service = None
    app.state.orchestrator_memory = None
    postgres_dsn = os.environ.get("POSTGRES_DSN", "")
    if postgres_dsn:
        try:
            import asyncpg  # noqa: PLC0415
            from orchestrator.core.plan_service import PlanService  # noqa: PLC0415
            from orchestrator.core.orchestrator_memory import OrchestratorMemoryService  # noqa: PLC0415

            db_pool = await asyncpg.create_pool(postgres_dsn, min_size=1, max_size=5)
            app.state.db_pool = db_pool

            # Run migrations (idempotent CREATE TABLE IF NOT EXISTS)
            migration_sql = _MIGRATION_PATH.read_text(encoding="utf-8")
            migration_003_sql = _MIGRATION_003_PATH.read_text(encoding="utf-8")
            async with db_pool.acquire() as conn:
                await conn.execute(migration_sql)
                await conn.execute(migration_003_sql)

            app.state.plan_service = PlanService(db_pool)
            app.state.orchestrator_memory = OrchestratorMemoryService(db_pool)
            logger.info("plan_service_ready")
            logger.info("orchestrator_memory_ready")
        except Exception as exc:
            logger.warning(
                "plan_service_unavailable",
                extra={"error": str(exc)},
            )

    yield

    await registry.stop()
    await _redis_pool.aclose()
    if app.state.db_pool is not None:
        await app.state.db_pool.close()


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
    deps: dict[str, str] = {"redis": "ok" if redis_ok else "error"}
    if request.app.state.db_pool is not None:
        deps["postgres"] = "ok"
    return JSONResponse({"status": status, "dependencies": deps}, status_code=code)


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
        registry=request.app.state.registry,
        plan_service=getattr(request.app.state, "plan_service", None),
        tenant_id=tenant_id,
        orchestrator_memory=getattr(request.app.state, "orchestrator_memory", None),
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


# ---------------------------------------------------------------------------
# GET /plans/{session_id}/current — latest plan for a session
# ---------------------------------------------------------------------------


@app.get("/plans/{session_id}/current", tags=["plans"])
async def get_current_plan(session_id: str, request: Request) -> dict[str, Any]:
    """
    Return the most recent plan for the given session_id.

    Frontend polls this endpoint every 1–2s for real-time progress display.
    """
    plan_service = getattr(request.app.state, "plan_service", None)
    if plan_service is None:
        raise HTTPException(status_code=503, detail="Plan service unavailable")

    row = await plan_service.db.fetchrow(
        "SELECT id FROM plans WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
        session_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No plan found for this session")

    plan_data = await plan_service.get_plan(str(row["id"]))
    if not plan_data:
        raise HTTPException(status_code=404, detail="Plan not found")

    return plan_data


# ---------------------------------------------------------------------------
# GET /plans/{plan_id} — plan detail by ID
# ---------------------------------------------------------------------------


@app.get("/plans/{plan_id}", tags=["plans"])
async def get_plan(plan_id: str, request: Request) -> dict[str, Any]:
    """
    Return a plan and its sub_goals by plan_id.
    """
    plan_service = getattr(request.app.state, "plan_service", None)
    if plan_service is None:
        raise HTTPException(status_code=503, detail="Plan service unavailable")

    plan_data = await plan_service.get_plan(plan_id)
    if not plan_data:
        raise HTTPException(status_code=404, detail="Plan not found")

    return plan_data

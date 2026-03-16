"""Tool Registry v1 — FastAPI application factory."""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from shared.auth_context import AuthForwardMiddleware

logger = logging.getLogger(__name__)

# Global reference to the active config loader — set during lifespan startup
_config_loader = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup: load YAML config and start watchdog. Shutdown: stop watchdog."""
    global _config_loader
    from tool_registry.config_loader import ConfigLoader

    # Import handlers to auto-register them
    import tool_registry.handlers.bi_query_handler  # noqa: F401

    loader = ConfigLoader()
    loader.load()
    loader.start_watcher()
    _config_loader = loader
    app.state.config_loader = loader
    logger.info("Tool Registry started. Tools loaded: %d", loader.store.tool_count)

    yield

    loader.stop_watcher()
    logger.info("Tool Registry stopped.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Tool Registry",
        description="Agentic SaaS Tool Registry v1 — YAML config, dual dispatch",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Auth forwarding middleware — reads Authorization + X-Tenant-Id into ContextVar
    app.add_middleware(AuthForwardMiddleware)

    # JSON structured logging middleware
    @app.middleware("http")
    async def logging_middleware(request: Request, call_next) -> Response:
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)
        # Token is intentionally EXCLUDED from log output
        logger.info(
            "request",
            extra={
                "agent_id": "tool-registry",
                "trace_id": trace_id,
                "action": f"{request.method} {request.url.path}",
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Trace-Id"] = trace_id
        return response

    # Routers
    from tool_registry.routers import health, tools

    app.include_router(tools.router)
    app.include_router(health.router)

    return app


app = create_app()

if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("TOOL_REGISTRY_PORT", "8001"))
    uvicorn.run("tool_registry.main:app", host="0.0.0.0", port=port, reload=False)

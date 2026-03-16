"""GET /health endpoint — service health and config status."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tool_registry.models import HealthResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check(request: Request) -> JSONResponse:
    """Return service health status and loaded tool count."""
    try:
        loader = request.app.state.config_loader
        if loader.is_healthy:
            return JSONResponse(
                status_code=200,
                content=HealthResponse(
                    status="healthy",
                    tool_count=loader.store.tool_count,
                    config_path=loader.store.config_path,
                    last_reload=loader.store.loaded_at,
                ).model_dump(),
            )
        else:
            return JSONResponse(
                status_code=503,
                content=HealthResponse(
                    status="degraded",
                    tool_count=loader.store.tool_count,
                    config_path=loader.store.config_path,
                    last_reload=loader.store.loaded_at,
                    error=loader.startup_error or "Config load failed",
                ).model_dump(),
            )
    except AttributeError:
        return JSONResponse(
            status_code=503,
            content=HealthResponse(
                status="degraded",
                error="Config loader not initialised",
            ).model_dump(),
        )

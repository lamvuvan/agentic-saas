"""Structured JSON request logging middleware.

Logs each HTTP request as a single JSON line with:
  agent_id, agent_role, trace_id, tenant_id, action, method, path,
  status, duration_ms

Authorization headers are NEVER logged.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("access")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that emits one structured JSON log line per request.

    Attach via:
        app.add_middleware(StructuredLoggingMiddleware, agent_id="orchestrator", agent_role="orchestrator")
    """

    def __init__(self, app, agent_id: str = "unknown", agent_role: str = "unknown") -> None:
        super().__init__(app)
        self.agent_id = agent_id
        self.agent_role = agent_role

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()

        trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
        tenant_id = request.headers.get("X-Tenant-Id", "default")
        # Authorization header is intentionally NOT logged
        path = request.url.path
        method = request.method

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            _emit(
                agent_id=self.agent_id,
                agent_role=self.agent_role,
                trace_id=trace_id,
                tenant_id=tenant_id,
                action="request",
                method=method,
                path=path,
                status=500,
                duration_ms=duration_ms,
                error=str(exc),
            )
            raise

        duration_ms = int((time.monotonic() - start) * 1000)
        _emit(
            agent_id=self.agent_id,
            agent_role=self.agent_role,
            trace_id=trace_id,
            tenant_id=tenant_id,
            action="request",
            method=method,
            path=path,
            status=status,
            duration_ms=duration_ms,
        )

        # Propagate trace_id in response header
        response.headers["X-Trace-Id"] = trace_id
        return response


def _emit(**fields) -> None:
    """Write a single structured JSON log line."""
    logger.info(json.dumps(fields, ensure_ascii=False))

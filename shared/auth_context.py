"""Shared request-scoped auth context using ContextVar.

Token is stored per-request and NEVER written to any persistent store or log.
"""
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_token: ContextVar[str] = ContextVar("token", default="")
_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="")


def set_auth(token: str, tenant_id: str = "") -> None:
    """Store token and tenant_id in the current async context."""
    _token.set(token)
    _tenant_id.set(tenant_id)


def get_token() -> str:
    """Return the Bearer token for the current request. Empty string if absent."""
    return _token.get()


def get_tenant_id() -> str:
    """Return the tenant ID for the current request. Empty string if absent."""
    return _tenant_id.get()


class AuthForwardMiddleware(BaseHTTPMiddleware):
    """Extract Authorization and X-Tenant-Id headers and store in ContextVar.

    Token is read once per request and MUST NOT appear in logs or responses.
    """

    async def dispatch(self, request: Request, call_next):
        raw_auth = request.headers.get("Authorization", "")
        token = raw_auth.removeprefix("Bearer ").strip()
        tenant = request.headers.get("X-Tenant-Id", "")
        if token or tenant:
            set_auth(token, tenant)
        return await call_next(request)

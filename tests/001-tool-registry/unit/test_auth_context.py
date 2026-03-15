"""T007: Unit tests for shared/auth_context.py — must FAIL before implementation."""
import asyncio

import pytest


class TestAuthContext:
    def test_set_and_get_token(self):
        from shared.auth_context import get_token, set_auth

        set_auth("test-token-123", "tenant-001")
        assert get_token() == "test-token-123"

    def test_set_and_get_tenant_id(self):
        from shared.auth_context import get_tenant_id, set_auth

        set_auth("tok", "my-tenant")
        assert get_tenant_id() == "my-tenant"

    def test_default_empty_string_when_not_set(self):
        from shared.auth_context import get_tenant_id, get_token

        # In a fresh context (new event loop task) defaults should be empty string
        assert get_token() == ""
        assert get_tenant_id() == ""

    def test_contextvars_isolate_across_concurrent_tasks(self):
        """Two concurrent async tasks must not share token state."""
        from shared.auth_context import get_token, set_auth

        results = {}

        async def task_a():
            set_auth("token-A", "tenant-A")
            await asyncio.sleep(0.01)  # yield to allow task_b to run
            results["a"] = get_token()

        async def task_b():
            set_auth("token-B", "tenant-B")
            await asyncio.sleep(0.01)
            results["b"] = get_token()

        async def run():
            await asyncio.gather(task_a(), task_b())

        asyncio.run(run())
        assert results["a"] == "token-A"
        assert results["b"] == "token-B"

    def test_token_stripped_of_bearer_prefix(self):
        """Middleware should strip 'Bearer ' prefix before storing."""
        from shared.auth_context import get_token, set_auth

        set_auth("raw-token", "t1")
        assert "Bearer" not in get_token()
        assert get_token() == "raw-token"


class TestAuthForwardMiddleware:
    @pytest.mark.asyncio
    async def test_middleware_sets_token_from_header(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from shared.auth_context import AuthForwardMiddleware, get_tenant_id, get_token

        app = FastAPI()
        app.add_middleware(AuthForwardMiddleware)

        captured = {}

        @app.get("/probe")
        async def probe():
            captured["token"] = get_token()
            captured["tenant"] = get_tenant_id()
            return {"ok": True}

        client = TestClient(app)
        client.get("/probe", headers={"Authorization": "Bearer mytoken", "X-Tenant-Id": "t99"})
        assert captured["token"] == "mytoken"
        assert captured["tenant"] == "t99"

    @pytest.mark.asyncio
    async def test_middleware_empty_when_no_headers(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from shared.auth_context import AuthForwardMiddleware, get_tenant_id, get_token

        app = FastAPI()
        app.add_middleware(AuthForwardMiddleware)

        captured = {}

        @app.get("/probe")
        async def probe():
            captured["token"] = get_token()
            captured["tenant"] = get_tenant_id()
            return {"ok": True}

        client = TestClient(app)
        client.get("/probe")
        assert captured["token"] == ""
        assert captured["tenant"] == ""

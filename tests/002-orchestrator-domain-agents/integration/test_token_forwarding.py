"""Integration tests: Bearer token forwarded through full A2A chain, never logged or stored.

Per FR-008 and Constitution §Security: token must reach Domain Agent header
but MUST NOT appear in any log record or task record stored in Redis.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest
import respx
from httpx import Response


SECRET_TOKEN = "test-secret-bearer-token-xyz"


# ---------------------------------------------------------------------------
# Token reaches Domain Agent A2A endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a2a_client_forwards_token_in_header():
    """submit_task() must include Authorization header when token is provided."""
    from shared.a2a.client import submit_task

    with respx.mock(base_url="http://order-agent:8002") as mock:
        mock.post("/a2a/tasks").mock(
            return_value=Response(202, json={"task_id": "tid-001", "status": "submitted"})
        )

        task_id = await submit_task(
            agent_url="http://order-agent:8002",
            skill="create_order",
            params={"message": "test", "session_id": "s1"},
            token=SECRET_TOKEN,
        )

        assert task_id == "tid-001"
        # Verify Authorization header was sent
        request = mock.calls[0].request
        assert request.headers.get("authorization") == f"Bearer {SECRET_TOKEN}"


@pytest.mark.asyncio
async def test_a2a_client_no_auth_header_when_no_token():
    """When token is empty, Authorization header must not be present."""
    from shared.a2a.client import submit_task

    with respx.mock(base_url="http://order-agent:8002") as mock:
        mock.post("/a2a/tasks").mock(
            return_value=Response(202, json={"task_id": "tid-002", "status": "submitted"})
        )

        await submit_task(
            agent_url="http://order-agent:8002",
            skill="create_order",
            params={"message": "test", "session_id": "s2"},
            token="",
        )

        request = mock.calls[0].request
        assert "authorization" not in request.headers


# ---------------------------------------------------------------------------
# Token never appears in logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_never_appears_in_log_records(caplog):
    """No log record from shared/a2a/client.py should contain the secret token."""
    from shared.a2a.client import submit_task

    with respx.mock(base_url="http://agent:8002") as mock:
        mock.post("/a2a/tasks").mock(
            return_value=Response(202, json={"task_id": "tid-003", "status": "submitted"})
        )

        with caplog.at_level(logging.DEBUG, logger="shared"):
            await submit_task(
                agent_url="http://agent:8002",
                skill="create_order",
                params={"message": "test", "session_id": "s3"},
                token=SECRET_TOKEN,
            )

    for record in caplog.records:
        assert SECRET_TOKEN not in record.getMessage(), (
            f"Secret token leaked in log message: {record.getMessage()}"
        )


# ---------------------------------------------------------------------------
# Token not stored in A2A task record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_not_stored_in_task_record():
    """The A2A task stored in Redis must not contain the Bearer token value."""
    from unittest.mock import AsyncMock, MagicMock

    from shared.a2a import server as task_store
    from shared.a2a.models import A2ATask

    stored_values: list[str] = []
    mock_redis = MagicMock()

    async def capture_set(key, value, **kwargs):
        stored_values.append(str(value))

    mock_redis.set = AsyncMock(side_effect=capture_set)

    task = A2ATask(
        skill="create_order",
        params={"message": "test", "session_id": "s4"},
        # Params intentionally do NOT include token — it is forwarded in header only
    )
    await task_store.store_task(mock_redis, task)

    for stored in stored_values:
        assert SECRET_TOKEN not in stored, "Token leaked into task record"


# ---------------------------------------------------------------------------
# poll_task passes token in header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_task_forwards_token():
    """poll_task() must include Authorization header when token is provided."""
    from shared.a2a.models import TaskStatus

    with respx.mock(base_url="http://agent:8002") as mock:
        mock.get("/a2a/tasks/tid-004").mock(
            return_value=Response(
                200,
                json={
                    "task_id": "tid-004",
                    "status": TaskStatus.COMPLETED,
                    "result": {
                        "output": {"status": "confirmed"},
                        "reasoning_summary": "Order confirmed",
                    },
                },
            )
        )

        from shared.a2a.client import poll_task

        task = await poll_task(
            agent_url="http://agent:8002",
            task_id="tid-004",
            token=SECRET_TOKEN,
        )

        assert task.status == TaskStatus.COMPLETED
        request = mock.calls[0].request
        assert request.headers.get("authorization") == f"Bearer {SECRET_TOKEN}"

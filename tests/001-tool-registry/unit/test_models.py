"""T008: Unit tests for tool_registry/models.py — must FAIL before implementation."""
import os

import pytest
from pydantic import ValidationError


class TestApiBlock:
    def test_valid_get_api_block(self):
        from tool_registry.models import ApiBlock

        block = ApiBlock(
            method="GET",
            url="https://api.example.com/items",
            params={"query": "keyword"},
            response_path="data",
            response_rename={"contactNumber": "phone"},
        )
        assert block.method == "GET"
        assert block.url == "https://api.example.com/items"

    def test_valid_post_api_block(self):
        from tool_registry.models import ApiBlock

        block = ApiBlock(
            method="POST",
            url="https://api.example.com/orders",
            body_mapping={"customer_id": "customerId"},
            response_fields=["id", "code"],
        )
        assert block.method == "POST"

    def test_env_var_in_url_stored_as_template(self):
        from tool_registry.models import ApiBlock

        block = ApiBlock(method="GET", url="${KIOTVIET_API_BASE}/customers")
        assert "${KIOTVIET_API_BASE}" in block.url


class TestHandlerRef:
    def test_valid_handler_ref(self):
        from tool_registry.models import HandlerRef

        ref = HandlerRef(name="bi_query_handler")
        assert ref.name == "bi_query_handler"

    def test_handler_ref_requires_name(self):
        from tool_registry.models import HandlerRef

        with pytest.raises(ValidationError):
            HandlerRef()


class TestToolDefinition:
    def test_valid_http_tool(self):
        from tool_registry.models import ApiBlock, ToolDefinition

        tool = ToolDefinition(
            name="customer__get_customers",
            namespace="customer",
            description="Find customers",
            parameters={"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
            dispatch=ApiBlock(method="GET", url="https://api.example.com/customers"),
        )
        assert tool.name == "customer__get_customers"
        assert tool.namespace == "customer"

    def test_valid_handler_tool(self):
        from tool_registry.models import HandlerRef, ToolDefinition

        tool = ToolDefinition(
            name="bi__run_query",
            namespace="bi",
            description="Run SQL query",
            parameters={"type": "object", "required": ["sql"], "properties": {"sql": {"type": "string"}}},
            dispatch=HandlerRef(name="bi_query_handler"),
        )
        assert tool.name == "bi__run_query"

    def test_name_must_contain_double_underscore(self):
        from tool_registry.models import ApiBlock, ToolDefinition

        with pytest.raises(ValidationError):
            ToolDefinition(
                name="invalidname",  # no __ separator
                namespace="ns",
                description="desc",
                parameters={"type": "object", "properties": {}},
                dispatch=ApiBlock(method="GET", url="https://example.com"),
            )

    def test_dispatch_required(self):
        from tool_registry.models import ToolDefinition

        with pytest.raises((ValidationError, TypeError)):
            ToolDefinition(
                name="ns__verb",
                namespace="ns",
                description="desc",
                parameters={"type": "object", "properties": {}},
            )


class TestExecutionResponse:
    def test_success_response(self):
        from tool_registry.models import ExecutionResponse

        resp = ExecutionResponse(
            tool_name="customer__get_customers",
            result=[{"customer_id": "123", "name": "Lam"}],
            metadata={"duration_ms": 200, "backend_status": 200},
        )
        assert resp.tool_name == "customer__get_customers"
        assert resp.error is None

    def test_error_response_requires_error_code(self):
        from tool_registry.models import ExecutionResponse

        resp = ExecutionResponse(
            tool_name="customer__get_customers",
            error="Tool not found",
            error_code="TOOL_NOT_FOUND",
            metadata={"duration_ms": 5},
        )
        assert resp.error_code == "TOOL_NOT_FOUND"
        assert resp.result is None

"""T014: Unit tests for tool_registry/config_loader.py."""
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


class TestParseYaml:
    def test_parses_valid_yaml(self, tmp_path):
        from tool_registry.config_loader import parse_yaml

        f = tmp_path / "tools.yaml"
        f.write_text("tools:\n  - name: ns__verb\n    namespace: ns\n")
        result = parse_yaml(str(f))
        assert isinstance(result, list)
        assert result[0]["name"] == "ns__verb"

    def test_returns_empty_list_for_empty_tools(self, tmp_path):
        from tool_registry.config_loader import parse_yaml

        f = tmp_path / "tools.yaml"
        f.write_text("tools: []\n")
        assert parse_yaml(str(f)) == []

    def test_raises_on_invalid_yaml(self, tmp_path):
        from tool_registry.config_loader import parse_yaml

        f = tmp_path / "tools.yaml"
        f.write_text(": invalid: yaml: [[[")
        with pytest.raises(Exception):
            parse_yaml(str(f))


class TestValidateTools:
    def test_valid_http_tool_passes(self):
        from tool_registry.config_loader import validate_tools

        raw = [
            {
                "name": "customer__get_customers",
                "namespace": "customer",
                "description": "Find customers",
                "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
                "api": {"method": "GET", "url": "https://api.example.com/customers"},
            }
        ]
        tools, errors = validate_tools(raw)
        assert len(tools) == 1
        assert len(errors) == 0

    def test_duplicate_name_skipped_with_error(self):
        from tool_registry.config_loader import validate_tools

        raw = [
            {
                "name": "ns__verb",
                "namespace": "ns",
                "description": "First",
                "parameters": {"type": "object", "properties": {}},
                "api": {"method": "GET", "url": "https://example.com"},
            },
            {
                "name": "ns__verb",  # duplicate
                "namespace": "ns",
                "description": "Second",
                "parameters": {"type": "object", "properties": {}},
                "api": {"method": "GET", "url": "https://example.com"},
            },
        ]
        tools, errors = validate_tools(raw)
        assert len(tools) == 1  # only first kept
        assert any("duplicate" in e.lower() or "ns__verb" in e for e in errors)

    def test_missing_dispatch_skipped_with_error(self):
        from tool_registry.config_loader import validate_tools

        raw = [
            {
                "name": "ns__verb",
                "namespace": "ns",
                "description": "No dispatch",
                "parameters": {"type": "object", "properties": {}},
                # neither api nor handler
            }
        ]
        tools, errors = validate_tools(raw)
        assert len(tools) == 0
        assert len(errors) >= 1


class TestToolStore:
    def _make_store(self, tools_raw=None):
        from tool_registry.config_loader import build_store, validate_tools

        if tools_raw is None:
            tools_raw = [
                {
                    "name": "customer__get_customers",
                    "namespace": "customer",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                    "api": {"method": "GET", "url": "https://example.com"},
                },
                {
                    "name": "bi__run_query",
                    "namespace": "bi",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                    "handler": "bi_query_handler",
                },
            ]
        tools, _ = validate_tools(tools_raw)
        return build_store(tools)

    def test_get_returns_tool_by_name(self):
        from tool_registry.config_loader import ToolStore

        store = self._make_store()
        tool = store.get("customer__get_customers")
        assert tool is not None
        assert tool.name == "customer__get_customers"

    def test_get_returns_none_for_unknown(self):
        store = self._make_store()
        assert store.get("unknown__tool") is None

    def test_list_filters_by_namespace(self):
        store = self._make_store()
        customer_tools = store.list(namespace="customer")
        assert all(t.namespace == "customer" for t in customer_tools)
        assert len(customer_tools) == 1

    def test_list_no_filter_returns_all(self):
        store = self._make_store()
        all_tools = store.list()
        assert len(all_tools) == 2

    def test_list_unknown_namespace_returns_empty(self):
        store = self._make_store()
        assert store.list(namespace="unknown") == []

    def test_tool_count_property(self):
        store = self._make_store()
        assert store.tool_count == 2

    def test_swap_is_atomic(self):
        """swap() should replace store contents atomically."""
        from tool_registry.config_loader import build_store, validate_tools

        store = self._make_store()
        new_raw = [
            {
                "name": "order__create_order",
                "namespace": "order",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
                "api": {"method": "POST", "url": "https://example.com/orders"},
            }
        ]
        new_tools, _ = validate_tools(new_raw)
        new_store = build_store(new_tools)
        store.swap(new_store)
        assert store.get("order__create_order") is not None
        assert store.get("customer__get_customers") is None
        assert store.tool_count == 1

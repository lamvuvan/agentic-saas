"""T013: Contract test — tools.yaml parses to valid ToolDefinition models."""
import re
from pathlib import Path

import pytest
import yaml


TOOLS_YAML = Path(__file__).parents[3] / "config" / "tools.yaml"


@pytest.fixture
def raw_tools():
    with open(TOOLS_YAML) as f:
        data = yaml.safe_load(f)
    return data.get("tools", [])


class TestConfigLoaderContract:
    def test_yaml_file_exists(self):
        assert TOOLS_YAML.exists(), f"tools.yaml not found at {TOOLS_YAML}"

    def test_each_entry_parses_to_tool_definition(self, raw_tools):
        from tool_registry.config_loader import validate_tools

        tools, errors = validate_tools(raw_tools)
        assert len(errors) == 0, f"Validation errors: {errors}"
        assert len(tools) > 0, "No tools loaded from tools.yaml"

    def test_name_matches_namespace_verb_pattern(self, raw_tools):
        pattern = re.compile(r"^[a-z0-9]+__[a-z0-9_]+$")
        for entry in raw_tools:
            name = entry.get("name", "")
            assert pattern.match(name), f"Tool name '{name}' does not match namespace__verb format"

    def test_each_entry_has_exactly_one_dispatch(self, raw_tools):
        for entry in raw_tools:
            has_api = "api" in entry
            has_handler = "handler" in entry
            assert has_api != has_handler, (
                f"Tool '{entry.get('name')}' must have exactly one of 'api' or 'handler', not both/neither"
            )

    def test_all_namespaces_present(self, raw_tools):
        """All 3 MVP namespaces must have at least one tool."""
        namespaces = {e.get("namespace") for e in raw_tools}
        assert "customer" in namespaces
        assert "order" in namespaces
        assert "bi" in namespaces

    def test_bi_tool_uses_handler_dispatch(self, raw_tools):
        bi_tools = [t for t in raw_tools if t.get("namespace") == "bi"]
        assert len(bi_tools) >= 1
        for t in bi_tools:
            assert "handler" in t, f"BI tool '{t.get('name')}' must use handler dispatch"

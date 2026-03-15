"""T018: Integration test for hot-reload — file change updates ToolStore within 5 s."""
import time
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def tools_yaml_content():
    return {
        "tools": [
            {
                "name": "ns__original",
                "namespace": "ns",
                "description": "Original tool",
                "parameters": {"type": "object", "properties": {}},
                "api": {"method": "GET", "url": "https://example.com"},
            }
        ]
    }


@pytest.fixture
def temp_yaml(tmp_path, tools_yaml_content):
    f = tmp_path / "tools.yaml"
    f.write_text(yaml.dump(tools_yaml_content))
    return f


class TestHotReload:
    def test_initial_load(self, temp_yaml):
        from tool_registry.config_loader import ConfigLoader

        loader = ConfigLoader(yaml_path=str(temp_yaml))
        loader.load()
        assert loader.store.tool_count == 1
        assert loader.store.get("ns__original") is not None

    def test_file_change_updates_store_within_5_seconds(self, temp_yaml):
        from tool_registry.config_loader import ConfigLoader

        loader = ConfigLoader(yaml_path=str(temp_yaml))
        loader.load()
        loader.start_watcher()

        try:
            # Write new tool definition
            new_content = {
                "tools": [
                    {
                        "name": "ns__new_tool",
                        "namespace": "ns",
                        "description": "New tool added by hot-reload",
                        "parameters": {"type": "object", "properties": {}},
                        "api": {"method": "GET", "url": "https://example.com/new"},
                    }
                ]
            }
            temp_yaml.write_text(yaml.dump(new_content))

            # Poll for up to 5 seconds
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if loader.store.get("ns__new_tool") is not None:
                    break
                time.sleep(0.1)

            assert loader.store.get("ns__new_tool") is not None, "Hot-reload did not update store within 5 s"
            assert loader.store.get("ns__original") is None
        finally:
            loader.stop_watcher()

    def test_invalid_yaml_retains_last_good_store(self, temp_yaml):
        from tool_registry.config_loader import ConfigLoader

        loader = ConfigLoader(yaml_path=str(temp_yaml))
        loader.load()
        loader.start_watcher()

        try:
            original_count = loader.store.tool_count
            assert original_count == 1

            # Write invalid YAML
            temp_yaml.write_text(": invalid: yaml: [[[\n")
            time.sleep(1.5)  # Allow watcher to fire

            # Store should be unchanged
            assert loader.store.tool_count == original_count
            assert loader.store.get("ns__original") is not None
        finally:
            loader.stop_watcher()

    def test_deleted_file_retains_last_good_store(self, temp_yaml):
        """If tools.yaml is deleted, retain last good config."""
        from tool_registry.config_loader import ConfigLoader

        loader = ConfigLoader(yaml_path=str(temp_yaml))
        loader.load()

        original_count = loader.store.tool_count

        # Manually trigger reload with non-existent file
        # (simulates delete scenario without watchdog needing to detect it)
        loader.yaml_path = str(temp_yaml.parent / "nonexistent.yaml")
        loader._reload()

        assert loader.store.tool_count == original_count

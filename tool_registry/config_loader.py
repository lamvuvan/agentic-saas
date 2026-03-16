"""YAML-based tool configuration loader with atomic hot-reload.

v1 design: tools.yaml is the sole source of truth. No database.
Hot-reload is atomic — agents see either the old valid config or the new one, never a mix.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from tool_registry.models import ApiBlock, HandlerRef, ToolDefinition

logger = logging.getLogger(__name__)


def _resolve_env_vars(url: str) -> str:
    """Replace ${VAR_NAME} placeholders in URL with environment variable values."""
    import re

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        value = os.environ.get(var_name, "")
        if not value:
            logger.warning("Environment variable '%s' not set — URL will be incomplete", var_name)
        return value

    return re.sub(r"\$\{([^}]+)\}", replacer, url)


def parse_yaml(path: str) -> list[dict[str, Any]]:
    """Load and parse tools.yaml. Returns list of raw tool dicts."""
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"tools.yaml must be a YAML mapping, got {type(data)}")
    return data.get("tools") or []


def _build_dispatch(raw: dict[str, Any]) -> ApiBlock | HandlerRef:
    """Build the dispatch object from raw YAML dict."""
    if "api" in raw and "handler" in raw:
        raise ValueError("Tool must have either 'api' or 'handler', not both")
    if "api" in raw:
        api_data = raw["api"].copy()
        # Resolve env vars in URL at load time
        if "url" in api_data:
            api_data["url"] = _resolve_env_vars(api_data["url"])
        return ApiBlock(**api_data)
    if "handler" in raw:
        return HandlerRef(name=raw["handler"])
    raise ValueError("Tool must have either 'api' or 'handler' field")


def validate_tools(raw_list: list[dict[str, Any]]) -> tuple[list[ToolDefinition], list[str]]:
    """Validate raw tool dicts into ToolDefinition models.

    Returns (valid_tools, error_messages). Invalid entries are skipped.
    Duplicate names are rejected (first occurrence wins).
    """
    tools: list[ToolDefinition] = []
    errors: list[str] = []
    seen_names: set[str] = set()

    for entry in raw_list:
        name = entry.get("name", "<unnamed>")
        try:
            dispatch = _build_dispatch(entry)
            tool = ToolDefinition(
                name=entry["name"],
                namespace=entry["namespace"],
                description=entry.get("description", ""),
                parameters=entry.get("parameters", {"type": "object", "properties": {}}),
                dispatch=dispatch,
            )
            if tool.name in seen_names:
                msg = f"Duplicate tool name '{tool.name}' — second entry skipped"
                logger.error(msg)
                errors.append(msg)
                continue
            seen_names.add(tool.name)
            tools.append(tool)
        except (ValidationError, ValueError, KeyError) as exc:
            msg = f"Invalid tool entry '{name}': {exc}"
            logger.error(msg)
            errors.append(msg)

    return tools, errors


def build_store(tools: list[ToolDefinition]) -> ToolStore:
    """Build an in-memory ToolStore from a validated list of ToolDefinitions."""
    store = ToolStore(config_path="")
    for tool in tools:
        store._tools[tool.name] = tool
        store._by_namespace.setdefault(tool.namespace, []).append(tool.name)
    store.loaded_at = datetime.now(UTC).isoformat()
    return store


class ToolStore:
    """Thread-safe in-memory store for tool definitions.

    The active store reference is swapped atomically on hot-reload.
    """

    def __init__(self, config_path: str) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._by_namespace: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        self.loaded_at: str = datetime.now(UTC).isoformat()
        self.config_path = config_path

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self, namespace: str | None = None) -> list[ToolDefinition]:
        if namespace is None:
            return list(self._tools.values())
        names = self._by_namespace.get(namespace, [])
        return [self._tools[n] for n in names if n in self._tools]

    def swap(self, new_store: ToolStore) -> None:
        """Atomically replace store contents from new_store."""
        with self._lock:
            self._tools = dict(new_store._tools)
            self._by_namespace = {k: list(v) for k, v in new_store._by_namespace.items()}
            self.loaded_at = new_store.loaded_at
            self.config_path = new_store.config_path


class _YamlEventHandler(FileSystemEventHandler):
    def __init__(self, loader: ConfigLoader) -> None:
        self._loader = loader
        self._debounce_timer: threading.Timer | None = None
        self._debounce_lock = threading.Lock()

    def on_modified(self, event) -> None:
        if event.is_directory:
            return
        if Path(event.src_path).resolve() != Path(self._loader.yaml_path).resolve():
            return
        # Debounce: wait briefly before reloading to handle editor save patterns
        with self._debounce_lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(0.2, self._loader._reload)
            self._debounce_timer.start()


class ConfigLoader:
    """Manages YAML config loading and watchdog-based hot-reload."""

    def __init__(self, yaml_path: str | None = None) -> None:
        self.yaml_path = yaml_path or os.getenv("TOOLS_YAML_PATH", "config/tools.yaml")
        self.store = ToolStore(config_path=self.yaml_path)
        self._observer: Observer | None = None
        self._startup_error: str | None = None

    def load(self) -> None:
        """Initial load at startup. Sets _startup_error on failure."""
        try:
            raw = parse_yaml(self.yaml_path)
            tools, errors = validate_tools(raw)
            new_store = build_store(tools)
            new_store.config_path = self.yaml_path
            self.store.swap(new_store)
            if errors:
                logger.warning("Loaded with %d skipped entries: %s", len(errors), errors)
            logger.info("Loaded %d tools from %s", self.store.tool_count, self.yaml_path)
        except Exception as exc:
            self._startup_error = str(exc)
            logger.error("Failed to load tools.yaml: %s", exc)

    def _reload(self) -> None:
        """Hot-reload: parse staging config, swap atomically if valid."""
        try:
            raw = parse_yaml(self.yaml_path)
            tools, errors = validate_tools(raw)
            new_store = build_store(tools)
            new_store.config_path = self.yaml_path
            self.store.swap(new_store)
            logger.info("Hot-reloaded %d tools from %s", self.store.tool_count, self.yaml_path)
            if errors:
                logger.warning("Hot-reload had %d skipped entries", len(errors))
        except Exception as exc:
            # Retain last good config — do NOT clear the store
            logger.error("Hot-reload failed, retaining previous config: %s", exc)

    def start_watcher(self) -> None:
        """Start the watchdog file watcher for hot-reload."""
        watch_dir = str(Path(self.yaml_path).parent.resolve())
        handler = _YamlEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, watch_dir, recursive=False)
        self._observer.start()
        logger.info("Watching %s for config changes", self.yaml_path)

    def stop_watcher(self) -> None:
        """Stop the watchdog observer."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    @property
    def is_healthy(self) -> bool:
        return self._startup_error is None and self.store.tool_count > 0

    @property
    def startup_error(self) -> str | None:
        return self._startup_error

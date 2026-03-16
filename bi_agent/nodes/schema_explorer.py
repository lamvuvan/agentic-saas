"""Schema explorer node — loads table allowlist from bi_schema.yaml for NL2SQL injection."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_SCHEMA_CONFIG_PATH = Path(os.environ.get("BI_SCHEMA_PATH", "config/bi_schema.yaml"))
_SCHEMA_CACHE: str | None = None


def load_schema_context() -> str:
    """
    Load and format schema context for NL2SQL prompt injection.
    Returns a string describing allowed tables, columns, and glossary.
    Cached after first load.
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE

    config_path = _SCHEMA_CONFIG_PATH
    if not config_path.exists():
        # Try relative to project root
        config_path = Path(__file__).parent.parent.parent / "config" / "bi_schema.yaml"

    if not config_path.exists():
        logger.warning("bi_schema_not_found", extra={"path": str(_SCHEMA_CONFIG_PATH)})
        return "Schema configuration not available."

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    lines: list[str] = ["## Available Tables\n"]

    for table_name, table_info in config.get("tables", {}).items():
        lines.append(f"**{table_name}**: {table_info.get('description', '')}")
        for col, desc in table_info.get("columns", {}).items():
            lines.append(f"  - {col}: {desc}")
        lines.append("")

    glossary = config.get("glossary", {})
    if glossary:
        lines.append("## Business Glossary (Vietnamese → SQL)\n")
        for term, definition in glossary.items():
            lines.append(f"- '{term}' → {definition}")

    _SCHEMA_CACHE = "\n".join(lines)
    return _SCHEMA_CACHE


def invalidate_schema_cache() -> None:
    """Clear schema cache to force reload on next request."""
    global _SCHEMA_CACHE
    _SCHEMA_CACHE = None

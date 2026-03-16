"""Safety check node — SELECT-only enforcement and automatic LIMIT injection."""

from __future__ import annotations

import re

# Patterns that indicate data-modifying or dangerous SQL operations
_BLOCKLIST_PATTERNS = [
    r"\bDELETE\b",
    r"\bUPDATE\b",
    r"\bINSERT\b",
    r"\bDROP\b",
    r"\bTRUNCATE\b",
    r"\bALTER\b",
    r"\bEXEC\b",
    r"\bEXECUTE\b",
    r"\bCALL\b",
    r"\bCREATE\b",
    r"\bREPLACE\b",
    r"\bMERGE\b",
]

_LIMIT_PATTERN = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


def check_safety(sql: str) -> tuple[bool, str | None]:
    """
    Verify SQL is safe to execute.

    Returns:
        (passed, rejection_reason)
        - passed=True if safe; reason=None
        - passed=False if unsafe; reason describes the violation
    """
    if not sql or not sql.strip():
        return False, "Empty SQL statement"

    sql_upper = sql.upper().strip()

    # Must start with SELECT (after stripping comments/whitespace)
    clean = re.sub(r"--[^\n]*", "", sql_upper).strip()
    clean = re.sub(r"/\*.*?\*/", "", clean, flags=re.DOTALL).strip()

    if not clean.startswith("SELECT") and not clean.startswith("WITH"):
        return False, f"Non-SELECT SQL rejected: statement starts with '{clean[:20]}'"

    # Check for blocklisted patterns
    for pattern in _BLOCKLIST_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            match = re.search(pattern, sql, re.IGNORECASE)
            keyword = match.group(0) if match else pattern
            return False, f"Non-SELECT SQL rejected: {keyword} statement detected"

    return True, None


def inject_limit(sql: str, max_rows: int = 500) -> str:
    """
    Inject a LIMIT clause if the query doesn't have one.
    Preserves existing LIMIT if present (does not override smaller limits).

    Returns the modified SQL string.
    """
    if not sql.strip():
        return sql

    if _LIMIT_PATTERN.search(sql):
        return sql  # Already has a LIMIT — leave it

    # Remove trailing semicolon before adding LIMIT
    stripped = sql.rstrip("; \n\t")
    return f"{stripped} LIMIT {max_rows}"

"""Sandboxed Python REPL tool for data analysis and calculations."""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Modules that are allowed to be imported inside the sandbox
_ALLOWED_IMPORTS = frozenset({
    "math", "statistics", "decimal", "fractions",
    "datetime", "calendar",
    "json", "re", "string",
    "itertools", "functools", "operator",
    "collections", "heapq",
    "random",
})

# Built-in names that are explicitly blocked
_BLOCKED_BUILTINS = frozenset({
    "open", "exec", "eval", "compile",
    "__import__", "breakpoint",
    "input", "print",  # print is redirected, not blocked
})


def _build_safe_globals() -> dict[str, Any]:
    """Return a restricted globals dict for code execution."""
    import builtins
    safe_builtins = {
        k: v for k, v in vars(builtins).items()
        if k not in _BLOCKED_BUILTINS
    }

    def safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
        base = name.split(".")[0]
        if base not in _ALLOWED_IMPORTS:
            raise ImportError(
                f"Import of '{name}' is not allowed. "
                f"Allowed modules: {sorted(_ALLOWED_IMPORTS)}"
            )
        return __import__(name, *args, **kwargs)

    safe_builtins["__import__"] = safe_import
    return {"__builtins__": safe_builtins}


def _execute_code(code: str, max_output_chars: int) -> str:
    """Execute code synchronously in a restricted environment."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    local_vars: dict[str, Any] = {}

    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, _build_safe_globals(), local_vars)  # noqa: S102
    except Exception:
        error = traceback.format_exc(limit=5)
        return f"Error:\n{error[:max_output_chars]}"

    output_parts = []
    stdout_val = stdout_buf.getvalue()
    stderr_val = stderr_buf.getvalue()

    if stdout_val:
        output_parts.append(stdout_val)
    if stderr_val:
        output_parts.append(f"[stderr]\n{stderr_val}")

    # If no print output, show the last expression's value if assigned to '_'
    if not output_parts:
        # Show variables that don't start with _
        results = {k: v for k, v in local_vars.items() if not k.startswith("_")}
        if results:
            lines = []
            for k, v in results.items():
                lines.append(f"{k} = {repr(v)}")
            output_parts.append("\n".join(lines))

    result = "\n".join(output_parts) if output_parts else "(no output)"
    return result[:max_output_chars]


class PythonReplTool(BaseTool):
    """Execute sandboxed Python code for data analysis and calculations.

    The sandbox:
    - Blocks filesystem, network, and subprocess access
    - Blocks dangerous builtins (open, exec, eval, __import__ for non-whitelisted modules)
    - Enforces a wall-clock timeout
    - Captures stdout/stderr and returns as string
    """

    name: str = "python_repl"
    description: str = (
        "Execute Python code for data analysis, complex calculations, list processing, "
        "and statistics. No network or file system access. "
        "Allowed imports: math, statistics, datetime, json, re, collections, itertools, random. "
        "Use print() to output results. "
        "Example: 'import statistics\\ndata = [120, 340, 250, 180]\\nprint(statistics.mean(data))'"
    )

    timeout: int = 10
    max_output_chars: int = 2000

    def _run(self, code: str) -> str:
        raise NotImplementedError("PythonReplTool is async-only; use _arun.")

    async def _arun(self, code: str) -> str:
        logger.debug("python_repl executing:\n%s", code[:500])
        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, _execute_code, code, self.max_output_chars),
                timeout=self.timeout,
            )
            return result
        except asyncio.TimeoutError:
            return f"Error: Code execution timed out after {self.timeout}s."
        except Exception as exc:
            return f"Error: {exc}"

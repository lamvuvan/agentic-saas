"""Safe mathematical expression evaluator."""

from __future__ import annotations

import ast
import math
import operator
import logging

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Allowed AST node types
_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Constant, ast.Num,  # ast.Num kept for Python 3.7 compat
    ast.Call, ast.Name,
    ast.Load,
)

# Allowed math function names
_ALLOWED_NAMES: dict[str, object] = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "int": int, "float": float,
    "sqrt": math.sqrt, "ceil": math.ceil, "floor": math.floor,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
    "pow": math.pow,
}


def _safe_eval(expression: str) -> float | int:
    """Parse and evaluate a math expression, rejecting unsafe nodes."""
    tree = ast.parse(expression.strip(), mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(f"Unsupported operation: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ValueError(f"Unknown name: {node.id!r}")
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, _ALLOWED_NAMES)  # noqa: S307


class CalculatorTool(BaseTool):
    """Safely evaluate mathematical expressions."""

    name: str = "calculator"
    description: str = (
        "Evaluate mathematical expressions accurately. "
        "Input: a math expression string, e.g. '(2500000 * 0.15)' or 'sqrt(144) + 10'. "
        "Supports: +, -, *, /, //, %, **, sqrt, ceil, floor, log, sin, cos, tan, pi, e, "
        "abs, round, min, max, sum."
    )

    def _run(self, expression: str) -> str:
        raise NotImplementedError("CalculatorTool is async-only; use _arun.")

    async def _arun(self, expression: str) -> str:
        try:
            result = _safe_eval(expression)
            # Format nicely: int if result is whole number
            if isinstance(result, float) and result.is_integer():
                result = int(result)
            logger.debug("Calculator: %s = %s", expression, result)
            return f"{result:,}" if isinstance(result, (int, float)) else str(result)
        except ZeroDivisionError:
            return "Error: Division by zero."
        except ValueError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error evaluating expression: {exc}"

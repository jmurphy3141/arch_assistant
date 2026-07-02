"""Native-only deterministic arithmetic with Decimal precision."""

from __future__ import annotations

import ast
import operator
from decimal import Decimal, DecimalException, localcontext
from typing import Any, Callable

from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}


def get_compute_tool_specs() -> tuple[ToolSpec, ...]:
    """Return the native deterministic calculator schema and handler."""
    return (
        ToolSpec(
            name="compute",
            handler=_handle_compute,
            description=(
                "Compute exact arithmetic for cost, TCO, proration, sizing, and "
                "percentage math (for example 16*0.03*730 for monthly compute, "
                "14/30.4 for POC proration, a 36-month projection, or a percentage). "
                "Use whenever an answer requires a calculation. Do not perform the "
                "arithmetic yourself."
            ),
            args={
                "expression": ArgSchema(
                    description="Arithmetic expression containing numbers and operators only.",
                    type="string",
                    required=True,
                )
            },
        ),
    )


async def _handle_compute(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,
    context: dict[str, Any],
    trace_id: str,
) -> ToolResult:
    expression = str(args.get("expression") or "").strip()
    try:
        if not expression:
            raise ValueError("Provide an arithmetic expression.")
        if len(expression) > 500:
            raise ValueError("Arithmetic expression is too long.")
        parsed = ast.parse(expression, mode="eval")
        with localcontext() as decimal_context:
            decimal_context.prec = 50
            value = _evaluate(parsed.body)
        normalized_expression = ast.unparse(parsed.body)
        result = _format_decimal(value)
    except (SyntaxError, ValueError, TypeError, ArithmeticError, DecimalException) as exc:
        return ToolResult(
            summary=f"Arithmetic expression rejected: {exc}",
            status="error",
            data={"expression": expression},
        )
    return ToolResult(
        summary=f"Computed {normalized_expression} = {result}.",
        status="ok",
        data={
            "result": result,
            "expression": normalized_expression,
            "normalized_expression": normalized_expression,
        },
    )


def _evaluate(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric constants are allowed.")
        return Decimal(str(node.value))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise ValueError("Exponent is outside the supported range.")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_evaluate(node.operand)
    raise ValueError("Only arithmetic numbers, parentheses, +, -, *, /, **, and % are allowed.")


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("Result must be finite.")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered

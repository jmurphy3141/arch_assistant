from decimal import Decimal

import pytest

from agent.compute_tools import get_compute_tool_specs


async def _compute(expression: str):
    spec = get_compute_tool_specs()[0]
    assert spec.name == "compute"
    return await spec.handler(
        {"expression": expression}, memory=None, context={}, trace_id="compute"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("16*0.03*730", Decimal("350.4")),
        ("(2+3)*4", Decimal("20")),
        ("14/30.4", Decimal("0.46052631578947368421052631578947368421052631578947")),
    ],
)
async def test_computes_decimal_arithmetic(expression: str, expected: Decimal) -> None:
    result = await _compute(expression)

    assert result.status == "ok"
    assert Decimal(result.data["result"]) == expected
    assert result.data["expression"]


@pytest.mark.asyncio
@pytest.mark.parametrize("expression", ['__import__("os")', "subtotal", "abs(-1)"])
async def test_rejects_non_arithmetic_without_raising(expression: str) -> None:
    result = await _compute(expression)

    assert result.status == "error"
    assert "rejected" in result.summary.lower()

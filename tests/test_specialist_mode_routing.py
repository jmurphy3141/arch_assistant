"""Routing tests for the current Forge/tool-handler specialist boundary."""
from __future__ import annotations

import asyncio

import pytest

from agent import archie_session
from agent.archie_wiring import build_forge
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.types import ToolResult


pytestmark = pytest.mark.integration


async def _runner(*_args, **_kwargs):
    return "done"


def test_required_specialist_tools_are_registered_on_forge():
    forge = build_forge(
        store=InMemoryObjectStore(),
        customer_id="routing-registry",
        customer_name="Routing Registry",
        text_runner=_runner,
        step3_planning=False,
    )
    assert {
        "generate_bom",
        "generate_diagram",
        "generate_pov",
        "generate_jep",
        "generate_waf",
        "generate_terraform",
        "generate_poc_plan",
    } <= set(forge._registry._tools)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Build the BOM.", {"generate_bom"}),
        ("Generate a diagram.", {"generate_diagram"}),
        ("Generate Terraform for my diagram.", {"generate_terraform"}),
        ("Draft a JEP using the finalized diagram.", {"generate_jep"}),
        ("Generate only the JEP artifact; do not build a BOM.", {"generate_jep"}),
        ("Build only the BOM. Do not generate a diagram or JEP.", {"generate_bom"}),
        ("Generate only the Diagram. Do not generate Terraform, WAF, or JEP.", {"generate_diagram"}),
        ("Explain one cost decision in that BOM. Do not regenerate it.", set()),
        ("Discuss whether VPN or FastConnect is better.", set()),
    ],
)
def test_generation_intent_is_bounded_to_explicit_output(message, expected):
    assert archie_session._requested_generation_tools(message) == expected


def test_invoke_tool_returns_toolresult_without_legacy_dispatch():
    forge = build_forge(
        store=InMemoryObjectStore(),
        customer_id="routing-invoke",
        customer_name="Routing Invoke",
        text_runner=_runner,
        step3_planning=False,
    )

    result = asyncio.run(forge.invoke_tool(
        "save_notes",
        {"text": "Private Java API in us-ashburn-1."},
        session_id="routing-invoke",
        context={},
    ))

    assert isinstance(result, ToolResult)
    assert result.status == "ok"


def test_selected_poc_confirmation_is_selection_only():
    from agent.tools.specialists import PocStrategistHandler
    from skillforge.types import MemorySnapshot

    option = {
        "option_name": "Private API Validation POC",
        "oci_services": ["VM.Standard.E5.Flex", "Site-to-Site VPN"],
        "grounding": {"region": "us-ashburn-1", "success_criteria": ["p95 under 500 ms"]},
    }
    context = {
        "archie": {"resolved_decisions": {"poc": {"options": [option]}}},
        "latest_decision_context": {"poc_options": [option]},
    }
    result = asyncio.run(PocStrategistHandler(
        InMemoryObjectStore(), "routing-selection", "Routing Selection"
    )(
        {"action": "confirm", "confirmed_option_name": option["option_name"]},
        memory=MemorySnapshot(session_id="routing-selection"),
        context=context,
        trace_id="selection-trace",
    ))

    assert result.status == "ok"
    assert result.parallel_tools is None
    assert result.data["selected_option_name"] == option["option_name"]

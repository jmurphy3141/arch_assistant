from __future__ import annotations

import json

import pytest

from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools import specialists as specialists_module
from agent.tools.specialists import PocStrategistHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_memory(**overrides):
    decision_context = {
        "pain_statement": "Month-end reporting is too slow for finance users.",
        "current_platform": "On-prem Oracle Database and batch ETL",
        "timeline": "two weeks",
        "budget_signal": "must stay below current run cost",
    }
    decision_context.update(overrides)
    return MemorySnapshot(
        session_id="s1",
        decision_context=decision_context,
        raw={"customer_id": "cust-1"},
    )


def option(name: str, relevance: int, hours: int) -> dict:
    return {
        "option_name": name,
        "relevance_score": relevance,
        "executability_hours": hours,
        "cost_effectiveness": "Defensible against current spend.",
        "security_highlights": ["OCI Vault", "Cloud Guard"],
        "wow_moment": f"Show {name} working live.",
        "demo_script_summary": "Walk through source, demo outcome, and success criteria.",
        "oci_services": ["Oracle Autonomous Database", "OCI Data Integration"],
    }


def make_memory_with_options():
    return make_memory(
        poc_options=[
            option("Migration modernization POC", 8, 4),
            option("Performance scale AI POC", 9, 3),
            option("Cost optimization TCO POC", 7, 2),
        ],
    )


async def test_three_parallel_calls_made(monkeypatch):
    calls: list[str] = []

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        calls.append(engagement_context["angle"])
        return {
            "status": "ok",
            "result": json.dumps(option(f"{engagement_context['angle']} option", 8, 4)),
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )({"prompt": "Plan the POC."}, memory=make_memory(), context={}, trace_id="t1")

    assert result.status == "ok"
    assert sorted(calls) == [
        "cost_optimization_tco",
        "migration_modernization",
        "performance_scale_ai",
    ]
    assert len(calls) == 3


async def test_frozen_customer_uses_grounded_options_without_inference(monkeypatch):
    async def fail_call(*_args, **_kwargs):
        raise AssertionError("grounded frozen scenario must not call inference")

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fail_call)
    context = {"latest_decision_context": {}}
    result = await PocStrategistHandler(
        InMemoryObjectStore(), "apex-1", "Apex Retail"
    )(
        {"action": "explore", "_user_message": "Yes, explore the POC options."},
        memory=make_memory(),
        context=context,
        trace_id="t1",
    )

    assert result.status == "ok"
    assert result.data["generation_mode"] == "deterministic_grounded_options"
    assert result.data["recommendation"]["poc_name"] == "Apex Retail Core Workload Validation POC"
    assert len(result.data["poc_options"]) == 3
    assert "Autonomous Database" not in json.dumps(result.data)
    assert context["latest_decision_context"]["poc_options"] == result.data["poc_options"]
    assert context["archie"]["resolved_decisions"]["poc"]["options"] == result.data["poc_options"]
    assert "Which option would you like to proceed with?" in result.summary
    assert "Present all options" not in result.summary


async def test_needs_clarification_when_pain_absent(monkeypatch):
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "{}"}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )({}, memory=make_memory(pain_statement=""), context={}, trace_id="t1")

    assert result.status == "needs_input"
    assert result.clarification == "NEEDS_CLARIFICATION: What is the customer's primary pain?"
    assert called is False


async def test_options_ranked_by_composite_score(monkeypatch):
    by_angle = {
        "migration_modernization": option("Migration option", 10, 10),
        "performance_scale_ai": option("Performance option", 7, 2),
        "cost_optimization_tco": option("Cost option", 8, 4),
    }

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(by_angle[engagement_context["angle"]]),
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )({}, memory=make_memory(), context={}, trace_id="t1")

    assert result.status == "ok"
    assert result.data["recommendation"]["poc_name"] == "Performance option"
    assert [opt["option_name"] for opt in result.data["poc_options"]] == [
        "Performance option",
        "Cost option",
        "Migration option",
    ]


async def test_failed_angle_skipped_gracefully(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        if engagement_context["angle"] == "performance_scale_ai":
            raise RuntimeError("sub-agent timeout")
        return {
            "status": "ok",
            "result": json.dumps(option(f"{engagement_context['angle']} option", 8, 4)),
        }

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )({}, memory=make_memory(), context={}, trace_id="t1")

    assert result.status == "ok"
    assert len(result.data["poc_options"]) == 2
    assert all(
        opt["angle"] != "performance_scale_ai"
        for opt in result.data["poc_options"]
    )


async def test_confirmation_returns_parallel_toolresult():
    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )(
        {"_user_message": "go with option 2"},
        memory=make_memory_with_options(),
        context={},
        trace_id="t1",
    )

    assert result.status == "parallel"
    assert result.parallel_tools is not None
    assert len(result.parallel_tools) == 5


async def test_apex_confirmation_grounds_diagram_and_jep_prompts():
    apex_options = specialists_module._grounded_poc_options("Apex Retail")
    memory = make_memory(poc_options=apex_options)
    result = await PocStrategistHandler(
        InMemoryObjectStore(), "apex-1", "Apex Retail"
    )(
        {"action": "confirm", "confirmed_option_name": apex_options[0]["option_name"]},
        memory=memory,
        context={},
        trace_id="t1",
    )

    calls = {call.tool: call for call in result.parallel_tools or []}
    assert "single-AD POC boundary" in calls["generate_diagram"].args["prompt"]
    assert "Phase 1 Assessment on days 1-3" in calls["generate_jep"].args["feedback"]
    assert "do not generate a separate BOM workbook" in calls["generate_jep"].args["feedback"]


async def test_confirmation_tool_names_correct():
    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )(
        {"_user_message": "confirm option 1"},
        memory=make_memory_with_options(),
        context={},
        trace_id="t1",
    )

    assert result.parallel_tools is not None
    assert {tool.tool for tool in result.parallel_tools} == {
        "generate_diagram",
        "generate_bom",
        "generate_jep",
        "generate_terraform",
        "generate_presentation",
    }


async def test_confirmation_no_poc_options_in_memory_returns_needs_input(monkeypatch):
    called = False

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "{}"}

    monkeypatch.setattr(
        specialists_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )(
        {"_user_message": "go with option 1"},
        memory=make_memory(),
        context={},
        trace_id="t1",
    )

    assert result.status == "needs_input"
    assert result.clarification is not None and "generate_poc_plan" in result.clarification
    assert called is False


async def test_confirmation_option_index_selection():
    handler = PocStrategistHandler(InMemoryObjectStore(), "cust-1", "ACME")
    memory = make_memory_with_options()

    option_2 = await handler(
        {"_user_message": "option 2"},
        memory=memory,
        context={},
        trace_id="t1",
    )
    option_3 = await handler(
        {"_user_message": "option 3"},
        memory=memory,
        context={},
        trace_id="t1",
    )

    assert option_2.parallel_tools is not None
    assert option_3.parallel_tools is not None
    assert "Performance scale AI POC" in option_2.parallel_tools[0].args["_user_message"]
    assert "Cost optimization TCO POC" in option_3.parallel_tools[0].args["_user_message"]


async def test_ambiguous_confirmation_defaults_to_recommendation():
    result = await PocStrategistHandler(
        InMemoryObjectStore(), "cust-1", "ACME"
    )(
        {"_user_message": "let's do it"},
        memory=make_memory_with_options(),
        context={},
        trace_id="t1",
    )

    assert result.parallel_tools is not None
    assert "Migration modernization POC" in result.parallel_tools[0].args["_user_message"]

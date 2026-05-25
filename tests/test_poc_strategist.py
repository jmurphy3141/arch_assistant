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

from __future__ import annotations

import json

import pytest

from agent import archie_session, poc_composer
from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools import specialists as specialists_module
from agent.tools.specialists import PocStrategistHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


GROUNDED_PROMPT = (
    "Redwood Logistics workload: on-premises Java shipment-tracking platform backed by PostgreSQL. "
    "Pain: release downtime and inconsistent peak performance. Target OCI us-phoenix-1 using a "
    "Flexible Load Balancer, three private VM.Standard.E5.Flex application servers, private PostgreSQL, "
    "Object Storage, Block Volume, Logging, Monitoring, and Site-to-Site VPN. POC duration: 15-day. "
    "Success criteria: p95 response under 400 milliseconds at 300 requests per second, 99.95% availability "
    "during a 48-hour soak test, and database restore within 60 minutes. Oracle SA and Redwood technical "
    "lead each commit 10 hours per week. Excluded: production cutover and multi-region DR."
)


def make_memory(**overrides):
    decision_context = {
        "pain_statement": "release downtime and inconsistent peak performance",
        "current_platform": "on-premises Java shipment-tracking platform backed by PostgreSQL",
    }
    decision_context.update(overrides)
    return MemorySnapshot(session_id="s1", decision_context=decision_context, raw={"customer_id": "cust-1"})


def options_for_memory():
    brief = poc_composer.build_poc_brief(
        customer_name="Redwood Logistics",
        user_message=GROUNDED_PROMPT,
        decision_context={},
    )
    assert brief is not None
    return poc_composer.compose_grounded_options(brief)


def test_brief_extracts_success_criteria_not_target_architecture():
    brief = poc_composer.build_poc_brief(
        customer_name="Redwood Logistics",
        user_message=GROUNDED_PROMPT,
        decision_context={},
    )

    assert brief is not None
    assert len(brief.success_criteria) == 3
    assert brief.success_criteria[0].startswith("p95 response under 400 milliseconds")
    assert all("Target OCI" not in criterion for criterion in brief.success_criteria)


async def test_novel_customer_uses_one_presentation_polish_call(monkeypatch):
    calls = []

    async def fake_call(name, task, engagement_context={}, trace_id=""):
        calls.append((name, engagement_context))
        base = json.loads(task)["options"]
        return {
            "status": "ok",
            "result": json.dumps({
                "options": [
                    {
                        "option_name": item["option_name"].replace("POC", "Proof"),
                        "demo_script_summary": item["demo_script_summary"],
                    }
                    for item in base
                ]
            }),
        }

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call)
    context = {"latest_decision_context": {}}
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "explore", "_user_message": GROUNDED_PROMPT},
        memory=make_memory(),
        context=context,
        trace_id="t1",
    )

    assert result.status == "ok"
    assert len(calls) == 1
    assert calls[0][1]["mode"] == "polish_options"
    assert result.data["generation_mode"] == "grounded_hybrid_options"
    assert result.data["polish_status"] == "polished"
    assert len(result.data["poc_options"]) == 3
    assert all(item["oci_services"] == result.data["poc_options"][0]["oci_services"] for item in result.data["poc_options"])
    assert "Autonomous Database" not in json.dumps(result.data)
    assert "$" not in json.dumps(result.data)
    assert context["archie"]["resolved_decisions"]["poc"]["artifact_key"] == result.artifact_key


async def test_unsupported_polish_falls_back_to_deterministic_options(monkeypatch):
    async def fake_call(*_args, **_kwargs):
        return {
            "status": "ok",
            "result": json.dumps({
                "options": [
                    {"option_name": "AI POC with $500 savings", "demo_script_summary": "Add Autonomous Database."}
                    for _ in range(3)
                ]
            }),
        }

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "explore", "_user_message": GROUNDED_PROMPT},
        memory=make_memory(), context={}, trace_id="t1",
    )

    assert result.status == "ok"
    assert result.data["polish_status"].startswith("fallback_")
    serialized = json.dumps(result.data)
    assert "Autonomous Database" not in serialized
    assert "$500" not in serialized


async def test_polish_failure_uses_deterministic_options(monkeypatch):
    async def fail(*_args, **_kwargs):
        raise RuntimeError("timeout")

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fail)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "explore", "_user_message": GROUNDED_PROMPT},
        memory=make_memory(), context={}, trace_id="t1",
    )
    assert result.status == "ok"
    assert result.data["polish_status"] == "fallback_error:RuntimeError"
    assert len(result.data["poc_options"]) == 3


async def test_missing_grounded_brief_needs_input_without_inference(monkeypatch):
    called = False

    async def fake_call(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "ACME")(
        {"action": "explore", "_user_message": "Give me POC ideas."},
        memory=make_memory(pain_statement=""), context={}, trace_id="t1",
    )
    assert result.status == "needs_input"
    assert called is False


async def test_confirmation_saves_selection_without_fanout():
    options = options_for_memory()
    memory = make_memory(poc_options=options)
    context = {}
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": options[1]["option_name"]},
        memory=memory, context=context, trace_id="t1",
    )
    assert result.status == "ok"
    assert result.parallel_tools is None
    assert result.data["selected_option_name"] == options[1]["option_name"]
    assert context["archie"]["resolved_decisions"]["poc"]["selected_option"] == options[1]


async def test_confirmation_accepts_option_number():
    options = options_for_memory()
    context = {}
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": "option 3"},
        memory=make_memory(poc_options=options), context=context, trace_id="t1",
    )

    assert result.status == "ok"
    assert result.data["selected_option"] == options[2]


async def test_confirmation_without_options_needs_input():
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "ACME")(
        {"action": "confirm", "confirmed_option_name": "Missing"},
        memory=make_memory(), context={}, trace_id="t1",
    )
    assert result.status == "needs_input"


async def test_confirmation_rejects_unknown_option_instead_of_selecting_first():
    options = options_for_memory()
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": "Unlisted AI Experiment"},
        memory=make_memory(poc_options=options), context={}, trace_id="t1",
    )

    assert result.status == "needs_input"
    assert "exact name" in result.clarification


def test_full_fact_sheet_create_request_routes_directly_to_poc_exploration():
    assert archie_session._is_poc_exploration_confirmation_request(
        "Create three POC ideas for this application with slow inventory updates."
    )
    assert not archie_session._is_change_update_intent(
        "Create three POC ideas for this application with slow inventory updates."
    )
    assert not archie_session._is_poc_exploration_confirmation_request(
        "Create the XLSX BOM for the selected POC."
    )


def test_bare_option_number_resolves_against_persisted_options():
    options = options_for_memory()
    context = {"latest_decision_context": {"poc_options": options}}

    assert archie_session._confirmed_poc_option_name("option 3", context) == options[2]["option_name"]

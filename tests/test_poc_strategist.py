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


def context_with_options(options):
    return {
        "archie": {"resolved_decisions": {"poc": {"options": options}}},
        "latest_decision_context": {"poc_options": options},
    }


@pytest.mark.parametrize(
    ("customer_name", "prompt"),
    [
        (
            "HarborStone",
            "HarborStone is an on-premises Java payments application backed by PostgreSQL. "
            "Goal: reduce release risk. Target OCI us-ashburn-1 with VM.Standard.E5.Flex, "
            "PostgreSQL, Object Storage, Logging, and Site-to-Site VPN. The POC lasts 15 days. "
            "Acceptance criteria: p95 under 500 milliseconds and restore within 60 minutes. "
            "Oracle SA and HarborStone lead each own delivery and commit 10 hours per week.",
        ),
        (
            "NovaGrid",
            "Migration of NovaGrid's grid telemetry service from VMware to OCI us-phoenix-1. "
            "Risks: unstable ingest latency. In scope: VM.Standard.E5.Flex, Object Storage, "
            "Block Volume, Logging, and Monitoring. POC lasts 15 days. Measurable success criteria: "
            "process 300 requests per second and recover within 30 minutes. Oracle SA and NovaGrid "
            "engineer will each contribute 8 hours per week.",
        ),
        (
            "Everwell",
            "Everwell currently runs a patient scheduling API on private virtual machines backed by "
            "PostgreSQL. It must prove predictable response time. Use OCI eu-frankfurt-1, a Flexible "
            "Load Balancer, VM.Standard.E5.Flex, PostgreSQL, Logging, and Monitoring. Duration is 12 days. "
            "Targets include p95 below 400 milliseconds and 99.9% availability. Delivery owners: "
            "Oracle SA and Everwell platform lead; commitment: 10 hours per week.",
        ),
        (
            "Argent",
            "Argent is moving its legacy claims-processing application to OCI uk-london-1. "
            "Objective: remove overnight batch overruns. Scope uses VM.Standard.E5.Flex, PostgreSQL, "
            "Object Storage, Block Volume, and Logging. Run the POC for 10 days. Performance targets: "
            "finish the batch within 2 hours and restore within 45 minutes. Oracle SA and Argent owner "
            "commit 9 hours each per week.",
        ),
        (
            "Pacifica",
            "Pacifica hosts an on-prem order-routing service backed by PostgreSQL. At risk from peak-hour "
            "timeouts. Validate in OCI ap-sydney-1 with a Flexible Load Balancer, VM.Standard.E5.Flex, "
            "PostgreSQL, Object Storage, Logging, and Monitoring. This proof of concept runs for 15 days. "
            "Desired outcomes: sustain 200 requests per second and keep p95 under 350 milliseconds. "
            "Oracle SA and Pacifica technical lead each provide 10 hours per week.",
        ),
    ],
)
def test_brief_accepts_natural_fact_sheet_variants(customer_name, prompt):
    brief = poc_composer.build_poc_brief(
        customer_name=customer_name,
        user_message=prompt,
        decision_context={},
    )

    assert brief is not None
    assert brief.workload
    assert brief.pain
    assert brief.duration_days > 0
    assert brief.owner_count == 2
    assert brief.success_criteria


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

    options = poc_composer.compose_grounded_options(brief)
    assert all(
        "Oracle SA" in option["grounding"]["owners"]
        and "Redwood technical lead" in option["grounding"]["owners"]
        and "10 hours per week" in option["grounding"]["owners"]
        for option in options
    )


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
        memory=make_memory(pain_statement="", current_platform=""), context={}, trace_id="t1",
    )
    assert result.status == "needs_input"
    assert called is False
    assert 1 <= len(result.data["questions"]) <= 3


async def test_customer_and_workload_create_tbd_draft_without_logistics(monkeypatch):
    async def fake_call(_name, task, engagement_context={}, trace_id=""):
        options = json.loads(task)["options"]
        return {
            "status": "ok",
            "result": json.dumps({
                "options": [
                    {
                        "option_name": option["option_name"],
                        "demo_script_summary": option["demo_script_summary"],
                    }
                    for option in options
                ]
            }),
        }

    monkeypatch.setattr(specialists_module.sub_agent_client, "call_sub_agent", fake_call)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Northwind Health")(
        {
            "action": "explore",
            "_user_message": "Northwind Health runs a .NET member portal. Draft POC options.",
        },
        memory=make_memory(
            pain_statement="",
            current_platform=".NET member portal",
        ),
        context={},
        trace_id="draft",
    )

    assert result.status == "ok"
    assert result.artifact_key
    for option in result.data["poc_options"]:
        assert option["executability_hours"] == "[TBD]"
        assert option["delivery_capacity_basis"] == "[TBD]"
        assert option["wow_moment"] == "[TBD]"
        assert option["grounding"]["duration_days"] == "[TBD]"
        assert option["grounding"]["owners"] == "[TBD]"
        assert option["grounding"]["success_criteria"] == ["[TBD]"]
        assert option["grounding"]["scope"] == ["[TBD]"]
        assert option["grounding"]["exclusions"] == ["[TBD]"]
    serialized = json.dumps(result.data)
    assert "Jordan Kim" not in serialized
    assert "20 days" not in serialized


def test_gradual_context_builds_targeted_brief_and_current_correction_wins():
    context = {
        "archie": {
            "engagement_summary": (
                "HarborStone runs an on-premises Java payments application backed by PostgreSQL. "
                "Goal: reduce release risk. Target OCI us-phoenix-1 with VM.Standard.E5.Flex, "
                "PostgreSQL, Object Storage, Logging, and Site-to-Site VPN. The POC lasts 15 days. "
                "Success criteria: p95 under 500 milliseconds and restore within 60 minutes. "
                "Oracle SA and HarborStone lead each commit 10 hours per week."
            ),
            "resolved_questions": [{"question": "Connectivity?", "answer": "Site-to-Site VPN"}],
            "latest_approved_constraints": {"public_edge": False},
            "latest_approved_assumptions": [{"region": "us-phoenix-1"}],
        },
        "sessions": {
            "ignored": {"history": [{"role": "user", "content": "Use Autonomous Database"}]}
        },
    }

    brief = poc_composer.build_poc_brief(
        customer_name="HarborStone",
        user_message="Correction: use us-ashburn-1, not the earlier region.",
        decision_context={},
        engagement_context=context,
    )

    assert brief is not None
    assert brief.region == "us-ashburn-1"
    assert "PostgreSQL DB System" in brief.allowed_services
    assert "Oracle Base Database Service" not in brief.allowed_services
    assert "Autonomous Database" not in brief.grounded_source


def test_gradual_discovery_summary_accumulates_without_raw_history():
    from agent import archie_memory

    context = {}
    for message in (
        "Workload: a VMware telemetry service with no database.",
        "Region us-phoenix-1; POC duration 15 days; Oracle SA and VoltForge engineer each commit 8 hours per week.",
        "In scope: Object Storage, Block Volume, Site-to-Site VPN, Logging, Monitoring, and E6 Flex compute. Success criteria: process 300 requests per second and recover within 30 minutes.",
    ):
        archie_memory._record_poc_discovery_context(context, message)

    brief = poc_composer.build_poc_brief(
        customer_name="VoltForge",
        user_message="Create three POC options.",
        decision_context={},
        engagement_context=context,
    )

    assert brief is not None
    assert brief.region == "us-phoenix-1"
    assert "VM.Standard.E6.Flex" in brief.allowed_services
    assert "PostgreSQL DB System" not in brief.allowed_services
    assert brief.success_criteria == (
        "process 300 requests per second",
        "recover within 30 minutes",
    )


def test_excluded_and_negated_services_are_not_admitted_to_poc_scope():
    prompt = (
        "AeroSpan runs a public order-routing service backed by PostgreSQL. Goal: reduce timeouts. "
        "Use OCI us-ashburn-1 with Flexible Load Balancer, E5 Flex, PostgreSQL, Object Storage, "
        "and Monitoring. POC duration 15 days. Success criteria: sustain 200 requests per second. "
        "Oracle SA and AeroSpan lead each commit 10 hours per week. Out of scope: OCI WAF. "
        "Connectivity is not FastConnect."
    )
    brief = poc_composer.build_poc_brief(
        customer_name="AeroSpan",
        user_message=prompt,
        decision_context={},
    )

    assert brief is not None
    assert "OCI WAF" not in brief.allowed_services
    assert "FastConnect" not in brief.allowed_services


async def test_confirmation_saves_selection_without_fanout():
    options = options_for_memory()
    memory = make_memory()
    context = context_with_options(options)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": options[1]["option_name"]},
        memory=memory, context=context, trace_id="t1",
    )
    assert result.status == "ok"
    assert result.parallel_tools is None
    assert result.data["selected_option_name"] == options[1]["option_name"]
    assert context["archie"]["resolved_decisions"]["poc"]["selected_option"] == options[1]
    contract = context["archie"]["consistency_contract"]
    assert contract["selected_poc"]["name"] == options[1]["option_name"]
    assert {item["service_id"] for item in contract["components"]} >= {
        "database.postgresql",
        "compute.vm.standard.e5.flex",
        "network.load_balancer.flexible",
    }


async def test_confirmation_accepts_option_number():
    options = options_for_memory()
    context = context_with_options(options)
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": "option 3"},
        memory=make_memory(), context=context, trace_id="t1",
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
        memory=make_memory(poc_options=[{"option_name": "transient wrong option"}]),
        context=context_with_options(options), trace_id="t1",
    )

    assert result.status == "needs_input"
    assert "exact name" in result.clarification


async def test_confirmation_rejects_out_of_range_option():
    options = options_for_memory()
    result = await PocStrategistHandler(InMemoryObjectStore(), "cust-1", "Redwood Logistics")(
        {"action": "confirm", "confirmed_option_name": "Option 4"},
        memory=make_memory(), context=context_with_options(options), trace_id="t1",
    )

    assert result.status == "needs_input"


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

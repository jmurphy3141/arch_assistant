import sys
import types

import pytest

from agent import consistency_contract
from agent.tools import diagram as diagram_module
from agent.tools.diagram import DiagramHandler
from agent.archie_session import _build_single_diagram_reply
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


def test_successful_diagram_reply_hides_internal_object_key():
    reply = _build_single_diagram_reply(
        {
            "result_summary": "Diagram generated. Key: agent3/acme/trace/v1/diagram.drawio",
            "artifact_key": "agent3/acme/trace/v1/diagram.drawio",
            "result_data": {"diagram_recovery_status": "none"},
        }
    )

    assert reply == "Done — the diagram is ready."


def test_bom_context_block_normalizes_quantitative_diagram_labels():
    block = diagram_module._diagram_bom_context_block(
        [
            {"description": "Storage - Block Volume - Storage", "quantity": 500, "metric": "GB"},
            {"description": "Storage - Block Volume - Performance Units", "quantity": 5000},
            {"description": "Object Storage - Storage", "quantity": 1024},
            {"description": "Web Application Firewall Policy", "quantity": 1},
        ]
    )

    assert "500 GB Balanced Block Volume" in block
    assert "5000 Performance Units for Block Volume" in block
    assert "1024 GB Object Storage" in block
    assert "OCI WAF Policy ×1" in block


def test_bom_coverage_gate_requires_all_quantitative_labels():
    line_items = [
        {"description": "Storage - Block Volume - Storage", "quantity": 500},
        {"description": "Storage - Block Volume - Performance Units", "quantity": 5000},
        {"description": "Object Storage - Storage", "quantity": 1024},
        {"description": "Web Application Firewall Policy", "quantity": 1},
    ]
    incomplete = '<mxfile><mxCell value="500 GB Balanced Block Volume"/><mxCell value="Object Storage"/></mxfile>'
    complete = (
        '<mxfile><mxCell value="500 GB Balanced Block Volume&#xa;5000 Performance Units"/>'
        '<mxCell value="1024 GB Object Storage"/><mxCell value="OCI WAF Policy ×1"/></mxfile>'
    )

    error = diagram_module._diagram_bom_coverage_error(line_items, incomplete)
    assert "5000 performance units" in error
    assert "Object Storage 1024 GB" in error
    assert "WAF policy" in error
    assert diagram_module._diagram_bom_coverage_error(line_items, complete) == ""


def test_diagram_consistency_gate_requires_poc_services_database_sizing_and_ha():
    requirements = [
        {"service_id": "network.load_balancer.flexible"},
        {"service_id": "compute.vm.standard.e5.flex"},
        {"service_id": "database.postgresql", "assumed_sizing": {"ocpu": 2}},
        {"service_id": "storage.object"},
        {"service_id": "storage.block"},
        {"service_id": "network.vpn.site_to_site"},
        {"service_id": "observability.logging"},
        {"service_id": "observability.monitoring"},
    ]
    incomplete = (
        '<mxfile><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="Autonomous Database"/><mxCell value="Object Storage"/></mxfile>'
    )
    complete = (
        '<mxfile><mxCell value="Single-AD POC Boundary"/>'
        '<mxCell value="Flexible Load Balancer"/><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="PostgreSQL 2 OCPU"/><mxCell value="Object Storage"/>'
        '<mxCell value="Block Volume"/><mxCell value="Site-to-Site VPN IPSec"/>'
        '<mxCell value="OCI Logging"/><mxCell value="OCI Monitoring"/></mxfile>'
    )

    error = diagram_module._diagram_consistency_error(requirements, "single-AD POC", incomplete)
    assert "missing PostgreSQL" in error
    assert "Flexible Load Balancer" in error
    assert "single-AD" in error
    assert diagram_module._diagram_consistency_error(requirements, "single-AD POC", complete) == ""


def test_diagram_consistency_gate_rejects_unselected_service_labels_and_wrong_region():
    requirements = [
        {"service_id": "compute.vm.standard.e5.flex"},
        {"service_id": "database.postgresql"},
    ]
    xml = (
        '<mxfile><mxCell value="Single-AD POC Boundary"/>'
        '<mxCell value="eu-frankfurt-1"/><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="PostgreSQL"/><mxCell value="OCI WAF"/></mxfile>'
    )

    error = diagram_module._diagram_consistency_error(
        requirements,
        "single-AD POC",
        xml,
        expected_region="us-ashburn-1",
    )

    assert "unexpected OCI WAF" in error
    assert "region us-ashburn-1 is not explicitly represented" in error


def test_diagram_consistency_gate_requires_one_private_subnet_per_tier():
    requirements = [{"service_id": "compute.vm.standard.e5.flex"}, {"service_id": "database.oracle"}]
    merged = (
        '<mxfile><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="Oracle Base Database Service"/>'
        '<mxCell value="Private Web and Application and Database Subnet"/></mxfile>'
    )
    separated = (
        '<mxfile><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="Oracle Base Database Service"/>'
        '<mxCell value="Private Web Subnet"/><mxCell value="Private Application Subnet"/>'
        '<mxCell value="Private Database Subnet"/></mxfile>'
    )

    error = diagram_module._diagram_consistency_error(
        requirements, "", merged, required_private_tiers=["web", "application", "database"],
    )
    assert "private subnet count 1 is fewer than required tiers 3" in error
    assert diagram_module._diagram_consistency_error(
        requirements, "", separated, required_private_tiers=["web", "application", "database"],
    ) == ""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_diagram_has_sufficient_context",
        lambda context, args, user_message: True,
    )


def make_handler():
    return DiagramHandler(
        store=object(),
        customer_id="cust-1",
        customer_name="ACME",
        text_runner=None,
        a2a_base_url="http://127.0.0.1:8000",
    )


def make_handler_with_store(store):
    return DiagramHandler(
        store=store,
        customer_id="cust-1",
        customer_name="ACME",
        text_runner=None,
        a2a_base_url="http://127.0.0.1:8000",
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


def install_archie_session_stub(monkeypatch, call_generate_diagram):
    module = types.ModuleType("agent.archie_session")
    module._call_generate_diagram = call_generate_diagram
    monkeypatch.setitem(sys.modules, "agent.archie_session", module)


async def test_diagram_ok(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        assert customer_id == "cust-1"
        assert a2a_base_url == "http://127.0.0.1:8000"
        return (
            "Diagram generated. Key: diagrams/foo.drawio",
            "diagrams/foo.drawio",
            {},
        )

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "diagrams/foo.drawio"


async def test_diagram_retries_then_persists_only_contract_consistent_output(monkeypatch):
    calls = []
    context = {}
    consistency_contract.record_selected_poc(context, {
        "option_name": "Skyline Operational Readiness POC",
        "oci_services": [
            "Flexible Load Balancer", "VM.Standard.E5.Flex", "PostgreSQL DB System",
            "Object Storage", "Block Volume", "Site-to-Site VPN", "Logging", "Monitoring",
        ],
        "grounding": {"region": "uk-london-1"},
    })
    context["archie"]["work_products"] = {
        "bom": {
            "latest": {
                "baseline": {
                    "line_items": [],
                    "scope_items": [{
                        "canonical_service_id": "database.postgresql",
                        "description": "PostgreSQL",
                        "assumed_sizing": {"ocpu": 2},
                    }],
                }
            }
        }
    }
    incomplete = '<mxfile><mxCell value="VM.Standard.E5.Flex"/></mxfile>'
    complete = (
        '<mxfile><mxCell value="Single-AD POC Boundary"/>'
        '<mxCell value="uk-london-1"/>'
        '<mxCell value="Flexible Load Balancer"/><mxCell value="VM.Standard.E5.Flex"/>'
        '<mxCell value="PostgreSQL 2 OCPU"/><mxCell value="Object Storage"/>'
        '<mxCell value="Block Volume"/><mxCell value="Site-to-Site VPN IPSec"/>'
        '<mxCell value="OCI Logging"/><mxCell value="OCI Monitoring"/></mxfile>'
    )

    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        calls.append(dict(args))
        xml = incomplete if len(calls) == 1 else complete
        return "Diagram generated.", "diagrams/skyline.drawio", {"drawio_xml": xml}

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)
    result = await make_handler()(
        {"prompt": "Generate the Diagram for the selected POC and finalized BOM."},
        memory=make_memory(),
        context=context,
        trace_id="skyline",
    )

    assert result.status == "ok"
    assert len(calls) == 2
    assert "CORRECTION FROM PYTHON VALIDATION" in calls[1]["prompt"]
    assert result.data["consistency_report"]["verdict"] == "pass"
    assert context["archie"]["consistency_contract"]["artifact_bindings"]["diagram"] == result.artifact_key


async def test_diagram_correction_preserves_authoritative_user_request(monkeypatch):
    captured = {}

    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        captured.update(args)
        return ("Diagram generated.", "diagrams/foo.drawio", {})

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)
    request = "Generate a diagram with two web servers and 500 GB Block Volume."
    result = await make_handler()(
        {
            "prompt": "Add Bastion and Active Directory.",
            "_user_message": request,
            "_forge_correction": "Include a route table.",
        },
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert request in captured["prompt"]
    assert "Add Bastion and Active Directory" not in captured["prompt"]
    assert "Do not add services, integrations, sizes" in captured["prompt"]


def test_diagram_content_review_requires_explicit_labels():
    request = (
        "Generate a diagram with two VM.Standard.E5.Flex web servers, PostgreSQL, "
        "500 GB Block Volume, NSGs, route tables, and a single-AD POC boundary."
    )
    incomplete = '<mxfile><mxCell value="PostgreSQL"/><mxCell value="500 GB Block Volume"/></mxfile>'
    complete = (
        '<mxfile><mxCell value="2 × VM.Standard.E5.Flex Web Servers"/>'
        '<mxCell value="PostgreSQL"/><mxCell value="500 GB Block Volume"/>'
        '<mxCell value="NSGs"/><mxCell value="Route Tables"/>'
        '<mxCell value="Single-AD POC Boundary"/></mxfile>'
    )

    assert "single-ad poc boundary" in diagram_module._diagram_content_error(request, incomplete)
    assert diagram_module._diagram_content_error(request, complete) == ""


async def test_diagram_persists_drawio_xml_when_sub_agent_returns_no_key(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        assert customer_id == "cust-1"
        assert a2a_base_url == "http://127.0.0.1:8000"
        return (
            "Diagram generated (task orch-1).",
            "",
            {"drawio_xml": "<mxfile />", "diagram_name": "orch-1"},
        )

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)
    store = InMemoryObjectStore()

    result = await make_handler_with_store(store)(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == "agent3/cust-1/orch-1/v1/diagram.drawio"
    assert store.get(result.artifact_key) == b"<mxfile />"


@pytest.mark.asyncio
async def test_diagram_wraps_raw_mxgraphmodel_before_persisting(monkeypatch):
    store = InMemoryObjectStore()

    async def _fake_call_generate_diagram(*_args, **_kwargs):
        return (
            "Diagram generated.",
            "",
            {"drawio_xml": '<mxGraphModel><root><mxCell id="0" /></root></mxGraphModel>'},
        )

    monkeypatch.setattr("agent.archie_session._call_generate_diagram", _fake_call_generate_diagram)

    handler = DiagramHandler(
        store=store,
        customer_id="acme",
        customer_name="ACME",
        text_runner=lambda *_args: "",
        a2a_base_url="http://localhost:8080",
    )

    result = await handler(
        {"bom_text": "Generate a diagram for VCN, subnet, load balancer, and database.", "diagram_name": "app"},
        memory=None,
        context={},
        trace_id="trace-1",
    )

    persisted = store.get(result.artifact_key).decode("utf-8")
    assert "<mxfile" in persisted
    assert "<mxGraphModel" in persisted
    assert result.data["object_key"] == result.artifact_key


async def test_diagram_insufficient_context(monkeypatch):
    called = False

    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        nonlocal called
        called = True
        return ("Diagram generated.", "diagrams/foo.drawio", {})

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_diagram_has_sufficient_context",
        lambda context, args, user_message: False,
    )

    result = await make_handler()(
        {"prompt": "draw it"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "needs_input"
    assert called is False


async def test_diagram_needs_clarification(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        return (
            "Clarify components.",
            "",
            {"diagram_recovery_status": "needs_clarification"},
        )

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Clarify components."


async def test_diagram_sub_agent_error(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        raise Exception("connection refused")

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"


async def test_diagram_failure_summary_without_artifact_is_blocked(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        return ("Diagram generation failed: connection refused", "", {})

    install_archie_session_stub(monkeypatch, fake_call_generate_diagram)

    result = await make_handler()(
        {"prompt": "draw it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert "failed" in result.summary.lower()

import json
import xml.etree.ElementTree as ET

import pytest

from sub_agents.diagram.server import handle
from sub_agents.diagram.server import _contract_layout_intent, _ground_layout_placement_layers
from sub_agents.models import A2ARequest


pytestmark = pytest.mark.anyio


def test_llm_layout_layers_are_grounded_to_parser_inventory():
    class Item:
        def __init__(self, item_id, layer):
            self.id = item_id
            self.layer = layer

    grounded = _ground_layout_placement_layers(
        {
            "placements": [
                {"id": "vpn_1", "oci_type": "vpn", "layer": "connectivity"},
                {"id": "db_1", "oci_type": "database", "layer": "infrastructure"},
            ]
        },
        [Item("vpn_1", "external"), Item("db_1", "data")],
    )

    assert grounded["placements"][0]["layer"] == "external"
    assert grounded["placements"][1]["layer"] == "data"


def test_contract_layout_intent_is_deterministic_and_preserves_vpn():
    class Item:
        def __init__(self, item_id, oci_type, layer):
            self.id = item_id
            self.oci_type = oci_type
            self.layer = layer

    items = [
        Item("lb_1", "load balancer", "ingress"),
        Item("app_1", "compute", "compute"),
        Item("db_1", "database", "data"),
        Item("vpn_1", "vpn", "external"),
        Item("waf_1", "waf", "ingress"),
    ]
    spec = _contract_layout_intent(
        (
            "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
            "- Flexible Load Balancer (network.load_balancer.flexible)\n"
            "- VM.Standard.E5.Flex (compute.vm.standard.e5.flex)\n"
            "- PostgreSQL (database.postgresql)\n"
            "- Site-to-Site VPN (network.vpn.site_to_site)\n"
            "[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
            "12 OCPU total across 3 instances (4 OCPU each). "
            "96 GB memory total across 3 instances (32 GB each)."
        ),
        items,
    )

    assert spec is not None
    assert spec["deployment_hints"]["on_prem_connectivity"] == "vpn"
    assert spec["deployment_hints"]["availability_domains_per_region"] == 1
    assert {item["id"] for item in spec["placements"]} == {"lb_1", "app_1", "db_1", "vpn_1"}
    assert "12 OCPU total / 4 OCPU each / 32 GB each" in items[1].label

    e6_items = [Item("app_1", "compute", "compute")]
    _contract_layout_intent(
        "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
        "- VM.Standard.E6.Flex (compute.vm.standard.e6.flex)\n"
        "[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
        "4 OCPU total across 2 instances (2 OCPU each).",
        e6_items,
    )
    assert "VM.Standard.E6.Flex ×2" in e6_items[0].label


def test_contract_layout_intent_injects_and_preserves_fastconnect():
    class Item:
        def __init__(self, item_id, oci_type, layer):
            self.id = item_id
            self.oci_type = oci_type
            self.layer = layer

    items = [
        Item("lb_1", "load balancer", "ingress"),
        Item("app_1", "compute", "compute"),
        Item("db_1", "database", "data"),
    ]
    spec = _contract_layout_intent(
        (
            "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
            "- Flexible Load Balancer (network.load_balancer.flexible)\n"
            "- VM.Standard.E5.Flex (compute.vm.standard.e5.flex)\n"
            "- PostgreSQL (database.postgresql)\n"
            "- FastConnect (network.fastconnect)\n"
            "[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]"
        ),
        items,
    )

    assert spec is not None
    assert spec["deployment_hints"]["on_prem_connectivity"] == "fastconnect"
    fastconnect = next(item for item in items if item.oci_type == "fastconnect")
    assert fastconnect.label == "FastConnect / DRG"
    assert fastconnect.id in {placement["id"] for placement in spec["placements"]}


def test_contract_layout_intent_preserves_authoritative_database_identity():
    class Item:
        def __init__(self, item_id, oci_type, layer, label):
            self.id = item_id
            self.oci_type = oci_type
            self.layer = layer
            self.label = label

    database = Item("db_1", "database", "data", "PostgreSQL Database")
    spec = _contract_layout_intent(
        (
            "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
            "- Oracle Base Database Service (database.oracle)\n"
            "[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]"
        ),
        [database],
    )

    assert spec is not None
    assert database.label == "Oracle Base Database Service"


async def test_contract_diagram_excludes_parser_defaults_and_labels_region(monkeypatch):
    def fail_inference(*_args, **_kwargs):
        raise AssertionError("contract topology must not call OCI inference")

    monkeypatch.setattr("sub_agents.diagram.server.run_inference", fail_inference)
    response = await handle(A2ARequest(task=(
        "[AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
        "Include and label every component exactly. HA mode: single-AD POC\n"
        "- VM.Standard.E5.Flex (compute.vm.standard.e5.flex)\n"
        "- PostgreSQL (database.postgresql)\n"
        "- Object Storage (storage.object)\n"
        "- Site-to-Site VPN (network.vpn.site_to_site)\n"
        "- OCI Logging (observability.logging)\n"
        "[/AUTHORITATIVE CROSS-ARTIFACT REQUIREMENTS]\n"
        "Generate the selected POC diagram in us-ashburn-1."
    )))

    assert response.status == "ok"
    assert "us-ashburn-1" in response.result
    assert 'value="WAF"' not in response.result
    assert "NAT Gateway" not in response.result
    assert "Internet Gateway" not in response.result


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_diagram_sub_agent_returns_needs_input_when_request_has_no_services():
    response = await handle(
        A2ARequest(
            task="I need the diagram updated to include the correct OCI Icons",
            trace_id="trace-1",
        )
    )

    assert response.status == "needs_input"
    questions = json.loads(response.result)
    assert [question["id"] for question in questions] == ["diagram.source", "workload.components"]
    assert response.trace["stage"] == "freeform_input_parsing"


async def test_detailed_explicit_diagram_uses_deterministic_fast_path(monkeypatch):
    def fail_inference(*_args, **_kwargs):
        raise AssertionError("explicit topology must not call OCI inference")

    monkeypatch.setattr("sub_agents.diagram.server.run_inference", fail_inference)
    response = await handle(
        A2ARequest(
            task=(
                "Generate a draw.io architecture diagram for Apex Retail in us-ashburn-1: "
                "Internet to OCI WAF to a public Flexible Load Balancer, then two "
                "VM.Standard.E5.Flex web servers in a private application subnet, then "
                "a private PostgreSQL database subnet. Include a VCN, Internet Gateway, "
                "NAT Gateway, Service Gateway, Object Storage, 500 GB Balanced Block "
                "Volume, NSGs, route tables, and a single-AD POC boundary."
            ),
            trace_id="trace-fast",
        )
    )

    assert response.status == "ok"
    assert response.trace["generation_mode"] == "deterministic_explicit_topology"
    ET.fromstring(response.result)
    root = ET.fromstring(response.result)
    lowered = response.result.lower()
    for expected in (
        "2 × vm.standard.e5.flex web servers",
        "private postgresql database",
        "500 gb balanced block volume",
        "nsgs: public lb, private app, private db",
        "route tables: public, private app, private db",
        "single-ad poc boundary",
    ):
        assert expected in lowered
    assert 'value="Availability Domain 1 — Single-AD POC Boundary"' in response.result
    assert 'id="poc_boundary_1"' not in response.result
    edges = {
        (cell.attrib.get("source"), cell.attrib.get("target"))
        for cell in root.iter()
        if cell.tag.endswith("mxCell") and cell.attrib.get("edge") == "1"
    }
    assert ("internet_g", "waf_1_g") in edges
    assert ("waf_1_g", "load_balancer_1_g") in edges
    assert ("load_balancer_1_g", "compute_1_g") in edges
    assert ("compute_1_g", "database_1_g") in edges

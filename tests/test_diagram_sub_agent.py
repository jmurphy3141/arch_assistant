import json
import xml.etree.ElementTree as ET

import pytest

from sub_agents.diagram.server import handle
from sub_agents.models import A2ARequest


pytestmark = pytest.mark.anyio


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

from __future__ import annotations

import json

import httpx
import pytest

from agent import sub_agent_client
from sub_agents.bom import server as bom_server
from sub_agents.diagram import server as diagram_server
from sub_agents.jep import server as jep_server
from sub_agents.models import A2ARequest
from sub_agents.pov import server as pov_server
from sub_agents.terraform import server as terraform_server
from sub_agents.waf import server as waf_server
from sub_agents.grounding import output_grounding_missing


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


_SERVERS = (
    pov_server,
    jep_server,
    bom_server,
    diagram_server,
    waf_server,
    terraform_server,
)


@pytest.mark.parametrize("server", _SERVERS, ids=lambda server: server.card.name)
async def test_missing_grounding_returns_needs_input_without_artifact(server):
    response = await server.handle(
        A2ARequest(
            task="Produce the requested artifact.",
            engagement_context={
                "customer_id": "northwind",
                "customer_name": "Northwind Health",
                "facts": {},
            },
            trace_id="missing-facts",
        )
    )

    assert response.status == "needs_input"
    assert response.trace["grounded"] is False
    assert "facts" in response.trace["missing"]
    assert "artifact_key" not in response.result


async def test_pov_and_jep_name_customer_and_report_grounded(monkeypatch):
    monkeypatch.setattr(
        pov_server,
        "run_inference",
        lambda *_args, **_kwargs: (
            "# Northwind Health OCI Point of View\n\n"
            "Northwind Health will validate its VMware migration assumptions on OCI."
        ),
    )
    context = {
        "customer_id": "northwind",
        "customer_name": "Northwind Health",
        "facts": {"platform": "VMware", "region": "us-ashburn-1"},
        "artifact_context": {
            "poc": {
                "selected_option_name": "Northwind Health Core Workload Validation POC",
                "oci_services": ["OCI WAF", "Flexible Load Balancer", "VM.Standard.E5.Flex"],
            }
        },
    }
    pov = await pov_server.handle(
        A2ARequest(
            task="Draft a concise internal OCI POV for the supplied engagement.",
            engagement_context=context,
            trace_id="pov-grounded",
        )
    )

    jep_task = (
        "Using the persisted POV and confirmed POC, create the final 14-day JEP and POC "
        "plan for Northwind Health to validate migration of an on-premises three-tier "
        "retail web application to OCI us-ashburn-1. Scope: WAF, public Flexible Load "
        "Balancer, two private "
        "VM.Standard.E5.Flex web servers, private PostgreSQL database, Object Storage, "
        "Block Volume, logging, and monitoring. Use exactly three phases: Phase 1 "
        "Assessment on days 1-3, Phase 2 Build on days 4-9, and Phase 3 Validate on days "
        "10-14. Success criteria: 99.9% availability during a 48-hour soak test, p95 "
        "response time under 500 milliseconds at 100 requests per second, and database "
        "restore within 60 minutes. Oracle SA and Northwind Health technical lead each "
        "commit 8 hours per week. Include at least three risks, a go/no-go sign-off with "
        "fallback, explicit out-of-scope items, a BOM section, timeline, owners, approvals, "
        "and handoff deliverables. Generate only the JEP artifact."
    )
    jep = await jep_server.handle(
        A2ARequest(task=jep_task, engagement_context=context, trace_id="jep-grounded")
    )

    assert pov.status == "ok"
    assert pov.trace["grounded"] is True
    assert "Northwind Health" in pov.result
    assert jep.status == "ok"
    assert jep.trace["grounded"] is True
    assert "Northwind Health" in jep.result


async def test_bom_diagram_waf_and_terraform_reflect_supplied_facts(monkeypatch):
    class _BomService:
        def generate_from_inputs(self, *, inputs, trace_id, model_id):
            return {
                "type": "final",
                "bom_payload": {
                    "region": inputs["region"],
                    "line_items": [{"description": "VM.Standard.E5.Flex"}],
                },
                "trace": {"cache_source": "test-cache"},
            }

    monkeypatch.setattr(bom_server, "get_shared_bom_service", lambda: _BomService())
    bom_context = {
        "customer_id": "northwind",
        "customer_name": "Northwind Health",
        "facts": {"region": "us-ashburn-1", "shape": "VM.Standard.E5.Flex"},
        "structured_inputs": {"region": "us-ashburn-1"},
    }
    bom = await bom_server.handle(
        A2ARequest(task="Build the BOM.", engagement_context=bom_context)
    )

    diagram_context = {
        "customer_id": "northwind",
        "customer_name": "Northwind Health",
        "facts": {"region": "us-ashburn-1", "shape": "VM.Standard.E5.Flex"},
    }
    diagram = await diagram_server.handle(
        A2ARequest(
            task=(
                "Generate a draw.io architecture diagram in us-ashburn-1. Include a VCN, "
                "Internet Gateway, NAT Gateway, Service Gateway, OCI WAF, public Flexible "
                "Load Balancer, private VM.Standard.E5.Flex application servers, private "
                "PostgreSQL database, Object Storage, Block Volume, NSGs, and route tables."
            ),
            engagement_context=diagram_context,
        )
    )

    monkeypatch.setattr(
        waf_server,
        "run_inference",
        lambda *_args, **_kwargs: (
            "# Northwind Health WAF Review\nThe us-ashburn-1 architecture uses VMware migration facts."
        ),
    )
    waf = await waf_server.handle(
        A2ARequest(task="Review the architecture.", engagement_context=diagram_context)
    )

    monkeypatch.setattr(
        terraform_server,
        "run_inference",
        lambda *_args, **_kwargs: json.dumps(
            {
                "main_tf": 'provider "oci" { region = "us-ashburn-1" }',
                "variables_tf": "",
                "outputs_tf": "",
                "readme_md": "# Northwind Health Terraform",
            }
        ),
    )
    terraform = await terraform_server.handle(
        A2ARequest(task="Generate Terraform.", engagement_context=diagram_context)
    )

    for response in (bom, diagram, waf, terraform):
        assert response.status == "ok"
        assert response.trace["grounded"] is True
        assert response.trace["missing"] == []


def test_structured_output_skips_prose_fact_anchor_but_keeps_input_grounding():
    context = {
        "customer_id": "northwind",
        "customer_name": "Northwind Health",
        "facts": {"latency_target": "600 milliseconds"},
    }

    assert output_grounding_missing(
        context,
        '<mxfile><object label="Application tier" /></mxfile>',
        output_kind="structured",
    ) == []
    assert output_grounding_missing(
        {**context, "facts": {}},
        '<mxfile><object label="Application tier" /></mxfile>',
        output_kind="structured",
    ) == ["facts"]
    assert "facts_not_reflected" in output_grounding_missing(
        context,
        "Northwind Health architecture overview.",
        require_customer_name=True,
    )


@pytest.mark.parametrize("server", _SERVERS, ids=lambda server: server.card.name)
async def test_client_sends_identity_and_facts_for_each_producer(
    server, monkeypatch
):
    seen: list[dict] = []

    async def card(_name):
        return server.card.model_dump()

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"result": "ok", "status": "ok", "trace": {}},
        )

    original_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(transport)
        return original_client(*args, **kwargs)

    monkeypatch.setattr(sub_agent_client, "get_agent_card", card)
    monkeypatch.setattr(sub_agent_client, "_sub_agent_url", lambda _name: "http://producer.test")
    monkeypatch.setattr(sub_agent_client.httpx, "AsyncClient", client)

    await sub_agent_client.call_sub_agent(
        server.card.name,
        "produce",
        engagement_context={
            "customer_id": "northwind",
            "customer_name": "Northwind Health",
            "archie": {"client_facts": {"region": "us-ashburn-1"}},
        },
    )

    sent = seen[0]["engagement_context"]
    assert sent["customer_id"] == "northwind"
    assert sent["customer_name"] == "Northwind Health"
    assert sent["facts"] == {"region": "us-ashburn-1"}

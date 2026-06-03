import json

import pytest

from agent.tools import bom as bom_module
from agent.tools import diagram as diagram_module
from agent.tools import specialists as specialists_module
from agent.tools import terraform as terraform_module
from agent.tools.bom import BomHandler
from agent.tools.diagram import DiagramHandler
from agent.tools.terraform import TerraformHandler
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def memory_arg_stubs(monkeypatch):
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_prepare_bom_tool_args",
        lambda args, user_message, context, decision_context: args,
    )
    for module in (bom_module, diagram_module, specialists_module, terraform_module):
        monkeypatch.setattr(
            module.archie_memory,
            "_hydrate_tool_args_from_context",
            lambda tool_name, args, context, decision_context, user_message: args,
        )
        monkeypatch.setattr(
            module.archie_memory,
            "_enforce_memory_contract_on_tool_args",
            lambda tool_name, args, context: args,
        )
    monkeypatch.setattr(
        diagram_module.archie_memory,
        "_diagram_has_sufficient_context",
        lambda context, args, user_message: True,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_has_architecture_definition",
        lambda context: True,
    )
    monkeypatch.setattr(
        terraform_module.archie_memory,
        "_terraform_scope_is_bounded",
        lambda context, args, decision_context, user_message: True,
    )


def make_memory(raw=None):
    return MemorySnapshot(
        session_id="s1",
        decision_context={},
        raw=raw or {"customer_id": "cust-1"},
    )


def bom_result(*, sku="B12345", monthly_total=730.0, artifact_key="bom.xlsx"):
    return {
        "artifact_key": artifact_key,
        "prices_from": "live_api",
        "bom_payload": {
            "monthly_total": monthly_total,
            "line_items": [
                {
                    "sku": sku,
                    "description": "Compute",
                    "quantity": 1,
                    "unit_price": 1,
                }
            ],
        },
    }


async def test_bom_rejects_wrong_monthly_total(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "ok", "result": json.dumps(bom_result(monthly_total=10))}

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)

    result = await BomHandler(object(), "cust-1", "ACME", None)(
        {"prompt": "size it"},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert "monthly_total arithmetic error" in result.data["validation_error"]


async def test_bom_flags_non_b_prefixed_sku(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "ok", "result": json.dumps(bom_result(sku="SKU-1"))}

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)

    result = await BomHandler(object(), "cust-1", "ACME", None)(
        {"prompt": "size it"},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.data["bom_payload"]["line_items"][0]["sku_unverified"] is True


async def test_terraform_rejects_hardcoded_ocid(monkeypatch):
    payload = {
        "artifact_key": "tf/main.tf",
        "files": {
            "main_tf": 'resource "x" "y" { compartment_id = "ocid1.compartment.oc1..abc" }',
            "variables_tf": 'variable "compartment_id" {}',
            "outputs_tf": 'output "id" { value = "x" }',
            "tfvars_example": 'compartment_id = "var.compartment_id"',
            "readme_md": "# Terraform",
        },
    }

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "ok", "result": json.dumps(payload)}

    monkeypatch.setattr(terraform_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)

    result = await TerraformHandler(object(), "cust-1", "ACME", None)(
        {"prompt": "make terraform"},
        memory=make_memory(),
        context={"agents": {}},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert "Hardcoded OCID found in main_tf" in result.data["validation_error"]


async def test_diagram_rejects_node_missing_subnet_tier(monkeypatch):
    async def fake_call_generate_diagram(args, customer_id, a2a_base_url):
        return (
            "Diagram generated.",
            "diagram.drawio",
            {
                "layout_intent": {
                    "nodes": [{"oci_type": "compute", "label": "App"}],
                    "subnet_tiers": ["private"],
                }
            },
        )

    import agent.archie_session as archie_session

    monkeypatch.setattr(archie_session, "_call_generate_diagram", fake_call_generate_diagram)

    result = await DiagramHandler(object(), "cust-1", "ACME", None)(
        {"prompt": "draw compute"},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "blocked"
    assert "node missing subnet_tier" in result.data["validation_error"]


async def test_bom_injects_confirmed_context(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured["task"] = task
        return {"status": "ok", "result": json.dumps(bom_result())}

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)

    result = await BomHandler(object(), "cust-1", "ACME", None)(
        {"prompt": "size it"},
        memory=make_memory(),
        context={"compute_shape": "E5.Flex"},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert "[CONFIRMED CONTEXT]" in captured["task"]
    assert "Shape: E5.Flex" in captured["task"]

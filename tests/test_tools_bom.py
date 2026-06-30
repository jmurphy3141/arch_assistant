import json

import pytest

from agent import consistency_contract, context_store, sub_agent_client
from agent.tools import bom as bom_module
from agent.tools.bom import BomHandler
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
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_hydrate_tool_args_from_context",
        lambda tool_name, args, context, decision_context, user_message: args,
    )
    monkeypatch.setattr(
        bom_module.archie_memory,
        "_enforce_memory_contract_on_tool_args",
        lambda tool_name, args, context: args,
    )


def make_handler():
    return BomHandler(
        store=object(),
        customer_id="cust-1",
        customer_name="ACME",
        text_runner=None,
    )


def make_memory():
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw={"customer_id": "cust-1"},
    )


def make_memory_with_raw(raw):
    return MemorySnapshot(
        session_id="s1",
        decision_context={"constraints": {"region": "us-ashburn-1"}},
        raw=raw,
    )


async def test_bom_ok(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        assert name == "bom"
        assert "[CONFIRMED CONTEXT]" in task
        assert task.endswith("size it")
        assert engagement_context == {"customer_id": "cust-1"}
        assert trace_id == "trace-1"
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "monthly_total": 730,
                        "totals": {"estimated_monthly_cost": 730},
                        "line_items": [
                            {
                                "sku": "B12345",
                                "description": "Compute",
                                "quantity": 1,
                                "unit_price": 1,
                            }
                        ],
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    context = {}
    result = await make_handler()(
        {"prompt": "size it"},
        memory=make_memory(),
        context=context,
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.data["bom_payload"]["line_items"][0]["sku"] == "B12345"
    assert context_store.latest_bom_work_product(context) is None


async def test_bom_injects_confirmed_context_before_scoped_correction(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured["task"] = task
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "line_items": [
                            {
                                "sku": "B12345",
                                "description": "Compute",
                                "quantity": 1,
                                "unit_price": 1,
                            }
                        ],
                        "monthly_total": 730,
                        "totals": {"estimated_monthly_cost": 730},
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it", "_forge_correction": "Use confirmed sizing."},
        memory=make_memory_with_raw(
            {
                "customer_id": "cust-1",
                "customer_name": "ACME",
                "instance_count": 3,
                "shapes": ["VM.Standard.E5.Flex"],
                "storage_gb": 1024,
            }
        ),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert captured["task"].startswith("[CONFIRMED CONTEXT]\n")
    assert "Shape: ['VM.Standard.E5.Flex']\n" in captured["task"]
    assert "Instance" not in captured["task"]
    assert captured["task"].index("[CONFIRMED CONTEXT]") < captured["task"].index(
        "[SCOPED REVIEW FEEDBACK]"
    )


async def test_bom_correction_preserves_authoritative_user_request(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured["task"] = task
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "line_items": [
                            {"sku": "B91961", "description": "Block", "quantity": 500, "unit_price": 1}
                        ],
                        "monthly_total": 500,
                        "totals": {"estimated_monthly_cost": 500},
                    },
                }
            ),
        }

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    request = "Create a BOM with 500 GB Balanced Block Volume."
    result = await make_handler()(
        {
            "prompt": "Create a BOM with invented 256 GB database storage.",
            "_user_message": request,
            "_forge_correction": "Add database storage.",
        },
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert request in captured["task"]
    assert "invented 256 GB" not in captured["task"]
    assert "Do not add services, sizes, quantities, storage" in captured["task"]


async def test_bom_accepts_correct_line_item_arithmetic(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "line_items": [
                            {
                                "sku": "B12345",
                                "description": "Compute",
                                "quantity": 2,
                                "unit_price": 1.0,
                            }
                        ],
                        "monthly_total": 1460.0,
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "ok"


async def test_bom_repairs_explicit_aggregate_sizing_before_success(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        rows = [
            {"sku": "B97384", "description": "E5 OCPU", "metric": "OCPU Per Hour", "quantity": 4, "unit_price": 0.03, "instance_count": 1},
            {"sku": "B97385", "description": "E5 Memory", "metric": "Gigabytes Per Hour", "quantity": 32, "unit_price": 0.002, "instance_count": 1},
            {"sku": "B91961", "description": "Block Storage", "metric": "GB Per Month", "quantity": 2048, "unit_price": 0.0255},
            {"sku": "B91628", "description": "Object Storage", "metric": "GB Per Month", "quantity": 5120, "unit_price": 0.0255},
        ]
        monthly = sum(row["quantity"] * row["unit_price"] * (730 if row["sku"] in {"B97384", "B97385"} else 1) for row in rows)
        return {
            "status": "ok",
            "result": json.dumps({
                "bom_payload": {
                    "region": "us-phoenix-1",
                    "line_items": rows,
                    "monthly_total": monthly,
                }
            }),
        }

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    request = (
        "Create a BOM in us-phoenix-1 for three private VM.Standard.E5.Flex application "
        "servers at 4 OCPUs and 32 GB RAM each, 2 TB Block Volume, and 5 TB Object Storage."
    )
    result = await make_handler()(
        {"prompt": request, "_user_message": request},
        memory=make_memory(), context={}, trace_id="trace-mismatch",
    )

    assert result.status == "ok"
    assert result.data["structured_inputs"]["memory"]["gb"] == 96
    assert result.data["sizing_repair"] == "deterministic_explicit_fields"
    assert result.data["bom_payload"]["region"] == "us-phoenix-1"
    by_sku = {row["sku"]: row for row in result.data["bom_payload"]["line_items"]}
    assert by_sku["B97384"]["quantity"] == 12
    assert by_sku["B97385"]["quantity"] == 96
    assert by_sku["B97384"]["instance_count"] == 3
    assert by_sku["B97385"]["instance_count"] == 3


async def test_selected_poc_services_survive_direct_sizing_and_cover_skyline_bom(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured["task"] = task
        captured["engagement_context"] = engagement_context
        rows = [
            {"sku": "B97384", "description": "Compute - Standard - E5 - OCPU", "metric": "OCPU Per Hour", "quantity": 15, "unit_price": 0.03, "instance_count": 3},
            {"sku": "B97385", "description": "Compute - Standard - E5 - Memory", "metric": "Gigabytes Per Hour", "quantity": 120, "unit_price": 0.002, "instance_count": 3},
            {"sku": "B91961", "description": "Storage - Block Volume - Storage", "metric": "GB Per Month", "quantity": 2048, "unit_price": 0.0255},
            {"sku": "B91962", "description": "Storage - Block Volume - Performance Units", "metric": "VPU Per Month", "quantity": 20480, "unit_price": 0.0017},
            {"sku": "B91628", "description": "Object Storage - Storage", "metric": "GB Per Month", "quantity": 6144, "unit_price": 0.0255},
        ]
        monthly = sum(
            row["quantity"] * row["unit_price"] * (730 if "Hour" in row["metric"] else 1)
            for row in rows
        )
        return {
            "status": "ok",
            "result": json.dumps({
                "bom_payload": {
                    "region": "uk-london-1",
                    "line_items": rows,
                    "monthly_total": monthly,
                }
            }),
        }

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    context = {}
    consistency_contract.record_selected_poc(context, {
        "option_name": "Skyline Operational Readiness POC",
        "oci_services": [
            "Flexible Load Balancer", "VM.Standard.E5.Flex", "PostgreSQL DB System",
            "Object Storage", "Block Volume", "Site-to-Site VPN", "Logging", "Monitoring",
        ],
        "grounding": {"region": "uk-london-1"},
    })
    request = (
        "Create the XLSX BOM for the selected POC in uk-london-1 with three private "
        "VM.Standard.E5.Flex application servers at 5 OCPUs and 40 GB RAM each, "
        "2 TB Block Volume, and 6 TB Object Storage."
    )

    result = await make_handler()(
        {"prompt": request, "_user_message": request},
        memory=make_memory_with_raw({"customer_id": "cust-1"}),
        context=context,
        trace_id="skyline",
    )

    assert result.status == "ok"
    assert captured["task"] == request
    handoff = captured["engagement_context"]["structured_inputs"]
    assert handoff["compute"]["ocpu"] == 15
    assert "PostgreSQL" in handoff["target_services"]
    assert result.data["consistency_report"]["verdict"] == "pass"
    coverage = {
        item["canonical_service_id"]
        for item in result.data["bom_payload"]["scope_items"]
    }
    assert {
        "database.postgresql", "network.vpn.site_to_site",
        "observability.logging", "observability.monitoring",
    } <= coverage
    assert "database.autonomous" not in result.data["consistency_report"]["represented_components"]
    assert any(
        row.get("canonical_service_id") == "database.postgresql"
        for row in result.data["bom_payload"]["line_items"]
    )


async def test_selected_poc_handoff_uses_contract_region_scope_and_poc_defaults(monkeypatch):
    captured = {}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        captured.update(engagement_context["structured_inputs"])
        rows = [
            {"sku": "B97384", "description": "E5 OCPU", "metric": "OCPU Per Hour", "quantity": 4, "unit_price": 0.03, "canonical_service_id": "compute.vm.standard.e5.flex"},
            {"sku": "B97385", "description": "E5 Memory", "metric": "Gigabytes Per Hour", "quantity": 32, "unit_price": 0.002, "canonical_service_id": "compute.vm.standard.e5.flex"},
            {"sku": "B91628", "description": "Object Storage", "metric": "GB Per Month", "quantity": 1024, "unit_price": 0.0255, "canonical_service_id": "storage.object"},
        ]
        total = sum(row["quantity"] * row["unit_price"] * (730 if "Hour" in row["metric"] else 1) for row in rows)
        return {"status": "ok", "result": json.dumps({"bom_payload": {
            "region": captured["region"], "line_items": rows, "monthly_total": total,
        }})}

    monkeypatch.setattr(bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent)
    context = {}
    consistency_contract.record_selected_poc(context, {
        "option_name": "Strict E5 POC",
        "oci_services": ["VM.Standard.E5.Flex", "Object Storage", "Logging"],
        "grounding": {"region": "us-phoenix-1"},
    })

    result = await make_handler()(
        {"prompt": "Create the selected POC BOM and use assumptions for missing sizing."},
        memory=make_memory_with_raw({"customer_id": "cust-1"}),
        context=context,
        trace_id="strict-handoff",
    )

    assert result.status == "ok"
    assert captured["region"] == "us-phoenix-1"
    assert captured["compute"]["shape"] == "VM.Standard.E5.Flex"
    assert captured["compute"]["ocpu"] == 4
    assert captured["memory"]["gb"] == 32
    assert captured["storage"]["object_gb"] == 1024
    assert set(captured["target_service_ids"]) == {
        "compute.vm.standard.e5.flex", "storage.object", "observability.logging",
    }


async def test_bom_accepts_valid_payload_before_xlsx_artifact_is_persisted(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "bom_payload": {
                        "line_items": [
                            {
                                "sku": "B12345",
                                "description": "Compute",
                                "quantity": 1,
                                "unit_price": 1.0,
                            }
                        ],
                        "monthly_total": 730.0,
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "ok"
    assert result.artifact_key == ""


async def test_bom_blocks_incorrect_line_item_arithmetic(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "line_items": [
                            {
                                "sku": "B12345",
                                "description": "Compute",
                                "quantity": 2,
                                "unit_price": 1.0,
                            }
                        ],
                        "monthly_total": 1314.0,
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert "monthly_total arithmetic error" in result.summary
    assert "validation_error" in result.data


async def test_bom_skips_arithmetic_check_for_empty_line_items(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {"line_items": [], "monthly_total": 1000.0},
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "blocked"
    assert "line_items is empty" in result.summary


async def test_bom_handler_enriches_prompt_requested_waf_and_database(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {
            "status": "ok",
            "result": json.dumps(
                {
                    "artifact_key": "bom.xlsx",
                    "bom_payload": {
                        "monthly_total": 119.72,
                        "line_items": [
                            {
                                "sku": "B94176",
                                "description": "Compute",
                                "category": "compute",
                                "metric": "OCPU Per Hour",
                                "quantity": 2,
                                "unit_price": 0.05,
                            },
                            {
                                "sku": "B94177",
                                "description": "Memory",
                                "category": "compute",
                                "metric": "Gigabytes Per Hour",
                                "quantity": 32,
                                "unit_price": 0.002,
                            },
                        ],
                    },
                }
            ),
        }

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {
            "prompt": (
                "Create an OCI XLSX bill of materials for a 3-tier web app with "
                "WAF, public load balancer, private database layer, Object Storage, and Block Volume."
            )
        },
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    skus = {row["sku"] for row in result.data["bom_payload"]["line_items"]}
    assert "BWAF01" in skus
    assert "B99060" in skus


async def test_bom_needs_input(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        return {"status": "needs_input", "result": "Please provide OCPU count."}

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert result.clarification == "Please provide OCPU count."


async def test_bom_direct_reply_shortcut(monkeypatch):
    called = False

    def fake_prepare(args, user_message, context, decision_context):
        return {**args, "_bom_direct_reply": "This is a followup without context."}

    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        nonlocal called
        called = True
        return {"status": "ok", "result": "{}"}

    monkeypatch.setattr(
        bom_module.archie_memory, "_prepare_bom_tool_args", fake_prepare
    )
    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert called is False


async def test_bom_sub_agent_error(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("connection refused")

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "size it"}, memory=make_memory(), context={}, trace_id="trace-1"
    )

    assert result.status == "needs_input"
    assert "local fallback needs" in result.summary


async def test_bom_sub_agent_error_uses_local_fallback_when_sizing_is_present(monkeypatch):
    async def fake_call_sub_agent(name, task, engagement_context={}, trace_id=""):
        raise sub_agent_client.SubAgentError("connection refused")

    monkeypatch.setattr(
        bom_module.sub_agent_client, "call_sub_agent", fake_call_sub_agent
    )

    result = await make_handler()(
        {"prompt": "Generate a BOM for 4 OCPU, 32 GB RAM, and 2 TB storage."},
        memory=make_memory(),
        context={},
        trace_id="trace-1",
    )

    assert result.status == "ok"
    assert result.data["bom_payload"]["line_items"]
    assert result.data["trace"]["fallback_used"] is True

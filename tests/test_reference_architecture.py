from __future__ import annotations

import asyncio

import drawing_agent_server as srv
from agent.bom_parser import ServiceItem
from agent.reference_architecture import (
    render_reference_architecture,
    select_reference_architecture,
)


def _single_region_oke_items() -> list[ServiceItem]:
    return [
        ServiceItem(id="waf_1", oci_type="waf", label="WAF", layer="ingress"),
        ServiceItem(id="lb_1", oci_type="load balancer", label="LB", layer="ingress"),
        ServiceItem(id="bastion_1", oci_type="bastion", label="Bastion", layer="ingress"),
        ServiceItem(id="oke_1", oci_type="container engine", label="OKE", layer="compute"),
        ServiceItem(id="db_1", oci_type="database", label="ATP", layer="data"),
        ServiceItem(id="obj_1", oci_type="object storage", label="Object Storage", layer="data"),
    ]


def _multi_region_oke_items() -> list[ServiceItem]:
    return [
        ServiceItem(id="lb_1", oci_type="load balancer", label="LB", layer="ingress"),
        ServiceItem(id="oke_1", oci_type="container engine", label="OKE", layer="compute"),
        ServiceItem(id="db_1", oci_type="database", label="ATP", layer="data"),
        ServiceItem(id="obj_1", oci_type="object storage", label="Object Storage", layer="data"),
    ]


def test_select_reference_architecture_prefers_single_region_oke_family() -> None:
    selection = select_reference_architecture(
        items=_single_region_oke_items(),
        deployment_hints={"availability_domains_per_region": 2},
    )

    assert selection.reference_mode == "reference-backed"
    assert selection.reference_family == "single_region_oke_app"
    assert selection.reference_confidence >= 0.72


def test_select_reference_architecture_prefers_multi_region_family() -> None:
    selection = select_reference_architecture(
        items=_multi_region_oke_items(),
        text="Multi-region SaaS deployment across Phoenix and Ashburn.",
        deployment_hints={"regions": ["us-phoenix-1", "us-ashburn-1"]},
    )

    assert selection.reference_mode == "reference-backed"
    assert selection.reference_family == "multi_region_oke_saas"
    assert selection.multi_region_mode in {"split_workloads", "duplicate_drha"}


def test_select_reference_architecture_falls_back_for_low_confidence_candidate() -> None:
    selection = select_reference_architecture(
        items=[ServiceItem(id="vm_1", oci_type="compute", label="VM", layer="compute")],
        text="Need a rough OCI diagram.",
    )

    assert selection.reference_mode == "best-effort-generic"
    assert selection.reference_family == ""


def test_render_reference_architecture_preserves_public_private_separation() -> None:
    selection = select_reference_architecture(
        items=_single_region_oke_items(),
        deployment_hints={"availability_domains_per_region": 2},
    )
    spec, metadata = render_reference_architecture(
        selection=selection,
        items=_single_region_oke_items(),
        deployment_hints={"availability_domains_per_region": 2},
    )

    assert spec["deployment_type"] == "multi_ad"
    assert spec["regions"][0]["regional_subnets"][0]["id"] == "public_ingress"
    private_subnets = spec["regions"][0]["availability_domains"][0]["subnets"]
    assert [subnet["id"] for subnet in private_subnets] == ["private_app", "private_data"]
    assert metadata["validation"]["overall_pass"] is True
    assert metadata["family_fit_score"] >= 0.9


def test_run_pipeline_uses_reference_backed_renderer_without_llm(monkeypatch) -> None:
    def _fail_llm(_prompt: str, _client_id: str) -> dict:
        raise AssertionError("reference-backed path should not call the LLM")

    monkeypatch.setattr(srv, "call_llm", _fail_llm)
    srv.app.state.object_store = None
    srv.app.state.persistence_config = {}

    result = asyncio.run(
        srv.run_pipeline(
            items=_single_region_oke_items(),
            prompt="unused for reference-backed path",
            diagram_name="reference-backed",
            client_id="ref-test",
            request_id="req-1",
            input_hash="hash-1",
            deployment_hints={"availability_domains_per_region": 2},
            reference_context_text="OKE web app with HA ingress and database",
        )
    )

    assert result["status"] == "ok"
    assert result["reference_architecture"]["reference_mode"] == "reference-backed"
    assert result["reference_architecture"]["reference_family"] == "single_region_oke_app"
    assert result["render_manifest"]["reference_mode"] == "reference-backed"

from __future__ import annotations

from agent import waf_agent
from agent.bom_parser import ServiceItem
from agent.diagram_waf_orchestrator import _build_diagram_context
from agent.persistence_objectstore import InMemoryObjectStore


def test_waf_fallback_suggestions_extracted_from_checklist() -> None:
    checklist = waf_agent._annotate_checklist(  # noqa: SLF001
        {
            "node_types": ["load_balancer", "compute"],
            "node_count": 2,
            "deployment_type": "single_ad",
        }
    )
    suggestions = waf_agent._fallback_suggestions_from_checklist(checklist)  # noqa: SLF001

    assert suggestions
    assert any("oci_type: waf" in s.get("draw_instruction", "") for s in suggestions)
    assert any("oci_type: monitoring" in s.get("draw_instruction", "") for s in suggestions)


def test_generate_waf_uses_fallback_when_suggestions_block_missing() -> None:
    store = InMemoryObjectStore()

    def _runner(_prompt: str, _system: str) -> str:
        return (
            "# WAF Review — Topology Gap Analysis\n\n"
            "## Failing / Warning Pillars\n"
            "Missing ingress protection and monitoring.\n\n"
            "---\n\n"
            "**Overall:** ❌ Critical Gaps\n"
        )

    result = waf_agent.generate_waf(
        customer_id="cust-a",
        customer_name="ACME",
        store=store,
        text_runner=_runner,
        diagram_context={
            "node_types": ["load_balancer", "compute"],
            "node_count": 2,
            "deployment_type": "single_ad",
        },
    )

    assert result["overall_rating"] == "❌"
    assert result["refinement_suggestions"]
    assert any("oci_type: waf" in s.get("draw_instruction", "") for s in result["refinement_suggestions"])


def test_build_diagram_context_uses_rendered_nodes_for_actual_presence() -> None:
    items = [
        ServiceItem(id="compute_1", oci_type="compute", label="Compute", layer="compute", notes="inline_bom"),
        ServiceItem(id="lb_1", oci_type="load balancer", label="LB", layer="ingress", notes="inline_bom"),
    ]
    draw_result = {
        "spec": {"deployment_type": "single_ad"},
        "draw_dict": {"nodes": [{"id": "compute_1", "type": "compute", "label": "Compute"}], "edges": []},
        "node_to_resource_map": {
            "compute_1": {"oci_type": "compute", "label": "Compute", "layer": "compute"},
            "lb_1": {"oci_type": "load balancer", "label": "LB", "layer": "ingress"},
        },
    }

    ctx = _build_diagram_context(draw_result, items)

    assert ctx["actual_node_types"] == ["compute"]
    assert ctx["expected_node_types"] == ["compute", "load balancer"]
    assert ctx["missing_expected_nodes"] == [
        {
            "id": "lb_1",
            "oci_type": "load balancer",
            "label": "LB",
            "layer": "ingress",
        }
    ]


def test_generate_waf_flags_bom_services_missing_from_rendered_diagram() -> None:
    store = InMemoryObjectStore()

    def _runner(_prompt: str, _system: str) -> str:
        return (
            "# WAF Review — Topology Gap Analysis\n\n"
            "## Failing / Warning Pillars\n"
            "Required BOM service missing from rendered diagram.\n\n"
            "---\n\n"
            "**Overall:** ❌ Critical Gaps\n"
        )

    result = waf_agent.generate_waf(
        customer_id="cust-b",
        customer_name="ACME",
        store=store,
        text_runner=_runner,
        diagram_context={
            "actual_node_types": ["compute"],
            "expected_node_types": ["compute", "load balancer"],
            "missing_expected_nodes": [
                {
                    "id": "lb_1",
                    "oci_type": "load balancer",
                    "label": "LB",
                    "layer": "ingress",
                }
            ],
            "node_count": 1,
            "deployment_type": "single_ad",
        },
    )

    assert result["overall_rating"] == "❌"
    assert any(
        "oci_type: load balancer" in s.get("draw_instruction", "")
        for s in result["refinement_suggestions"]
    )

from __future__ import annotations

from agent import waf_agent
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

from __future__ import annotations

import pytest

from agent import decision_context, governor_agent


pytestmark = pytest.mark.unit


def test_decision_context_extracts_constraints_and_tags() -> None:
    result = decision_context.build_decision_context(
        user_message=(
            "Design an OCI architecture in us-phoenix-1 with 99.99% availability, "
            "budget under $5000 monthly, private networking, and PCI controls."
        )
    )

    assert result["constraints"]["region"] == "us-phoenix-1"
    assert result["constraints"]["availability_target"] == "99.99%"
    assert result["constraints"]["cost_max_monthly"] == 5000.0
    assert "private-only networking" in result["constraints"]["security_requirements"]
    assert "pci" in result["constraints"]["compliance_requirements"]

    tags = decision_context.derive_constraint_tags(result)
    assert "cost_sensitive" in tags
    assert "ha_required" in tags
    assert "region_pinned" in tags
    assert "security_sensitive" in tags


def test_governor_promotes_budget_overrun_to_checkpoint() -> None:
    def _runner(_prompt: str, _system: str) -> str:
        return (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":92,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"Acceptable design.","reason_codes":[]}'
        )

    result = governor_agent.evaluate_tool_result(
        tool_name="generate_bom",
        user_message="Build BOM under $5000 monthly",
        tool_args={"prompt": "BOM request"},
        decision_context={
            "goal": "Build BOM",
            "constraints": {"cost_max_monthly": 5000},
            "assumptions": [],
            "success_criteria": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        },
        result_summary="Final BOM prepared.",
        result_data={"bom_payload": {"totals": {"estimated_monthly_cost": 6400}}},
        text_runner=_runner,
    )

    assert result["overall_status"] == "checkpoint_required"
    assert result["cost"]["status"] == "checkpoint_required"
    assert result["cost"]["variance"] == 1400.0
    assert "budget_exceeded" in result["reason_codes"]

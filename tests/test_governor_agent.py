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


def test_governor_uses_ten_percent_budget_tolerance() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_bom",
        user_message="Build BOM under $5000 monthly",
        tool_args={"prompt": "BOM request"},
        decision_context={"constraints": {"cost_max_monthly": 5000}, "assumptions": [], "missing_inputs": []},
        result_summary="Final BOM prepared.",
        result_data={"bom_payload": {"totals": {"estimated_monthly_cost": 5400}}},
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":92,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"Acceptable design.","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "pass"
    assert result["cost"]["status"] == "pass"
    assert "budget_exceeded" not in result["reason_codes"]


def test_governor_public_ingress_without_waf_requires_checkpoint() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_diagram",
        user_message="Design an internet-facing app server architecture.",
        tool_args={"bom_text": "public app"},
        decision_context={"constraints": {}, "assumptions": [], "missing_inputs": []},
        result_summary="Generated compute application with public ingress and no WAF.",
        result_data={"resources": [{"type": "compute", "public_ingress": True}]},
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":90,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "checkpoint_required"
    assert "public_ingress_without_waf" in result["reason_codes"]
    assert result["security"]["findings"]


def test_governor_root_compartment_and_missing_encryption_require_checkpoint() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_terraform",
        user_message="Generate Terraform.",
        tool_args={"prompt": "tf"},
        decision_context={"constraints": {}, "assumptions": [], "missing_inputs": []},
        result_summary="Terraform includes database and block volume resources.",
        result_data={
            "resources": [
                {"type": "oci_core_volume", "compartment_name": "root", "encrypted": False},
                {"type": "database", "encryption": "missing"},
            ]
        },
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":90,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "checkpoint_required"
    assert "root_compartment_usage" in result["reason_codes"]
    assert "missing_encryption" in result["reason_codes"]


def test_governor_single_resource_budget_concentration_is_warning_only() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_bom",
        user_message="Build BOM under $5000 monthly",
        tool_args={"prompt": "BOM request"},
        decision_context={"constraints": {"cost_max_monthly": 5000}, "assumptions": [], "missing_inputs": []},
        result_summary="Final BOM prepared.",
        result_data={
            "bom_payload": {
                "totals": {"estimated_monthly_cost": 5100},
                "line_items": [{"name": "Autonomous Database", "estimated_monthly_cost": 2300}],
            }
        },
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":90,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"Acceptable design.","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "pass"
    assert result["cost"]["status"] == "pass"
    assert "single_resource_budget_concentration" in result["reason_codes"]
    assert result["cost"]["findings"]


def test_governor_high_risk_assumption_with_missing_input_requires_checkpoint() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_pov",
        user_message="Draft POV with sparse notes.",
        tool_args={"feedback": "draft"},
        decision_context={
            "constraints": {},
            "assumptions": [{"statement": "Assume active-active DR.", "risk": "high"}],
            "missing_inputs": ["confirmed RTO/RPO"],
        },
        result_summary="POV saved. Key: pov/acme/v1.md",
        result_data={},
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":90,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "checkpoint_required"
    assert "high_risk_assumption_missing_input" in result["reason_codes"]


def test_governor_blocks_structured_requirement_contradiction() -> None:
    result = governor_agent.evaluate_tool_result(
        tool_name="generate_diagram",
        user_message="Use private-only ingress.",
        tool_args={"bom_text": "diagram"},
        decision_context={
            "constraints": {"security_requirements": ["private-only networking"]},
            "assumptions": [],
            "missing_inputs": [],
        },
        result_summary="Diagram generated. Key: diagrams/acme/v1/diagram.drawio",
        result_data={"spec": {"public_ingress": True}},
        text_runner=lambda *_args: (
            '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},'
            '"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},'
            '"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":90,"summary":"Acceptable","severity":"low"},'
            '"decision_summary":"","reason_codes":[]}'
        ),
    )

    assert result["overall_status"] == "blocked"
    assert result["security"]["status"] == "blocked"
    assert "requirement_contradiction" in result["reason_codes"]

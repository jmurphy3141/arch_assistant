from __future__ import annotations

from agent import decision_context


def test_ballpark_assumption_mode_does_not_require_confirmation_for_missing_budget_cap() -> None:
    result = decision_context.build_decision_context(
        user_message=(
            "I only got a small set of notes from the client. Need a ballpark BOM and diagram "
            "with standard safe assumptions for OCI."
        )
    )

    assumptions = {item["id"]: item for item in result["assumptions"]}
    assert "cost_unbounded" not in assumptions
    assert "monthly budget cap" not in result["missing_inputs"]
    assert result["requires_user_confirmation"] is False
    assert result["assumption_mode"] is True
    assert "high_risk_assumptions" not in decision_context.derive_constraint_tags(result)


def test_bom_without_budget_cap_is_advisory_not_high_risk() -> None:
    result = decision_context.build_decision_context(
        user_message="Generate a BOM and XLSX for 48 OCPU, 768 GB RAM, and 42 TB block storage."
    )

    assumptions = {item["id"]: item for item in result["assumptions"]}
    assert "cost_unbounded" not in assumptions
    assert "monthly budget cap" not in result["missing_inputs"]
    assert result["requires_user_confirmation"] is False
    assert "high_risk_assumptions" not in decision_context.derive_constraint_tags(result)


def test_architecture_chat_prompt_is_marked_conversational() -> None:
    result = decision_context.build_decision_context(
        user_message="Talk me through architecture tradeoffs for a private OKE platform."
    )

    assert result["conversational_architecture"] is True
    assert result["risk_level"] in {"low", "medium", "high"}


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

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
    assert assumptions["cost_unbounded"]["risk"] == "medium"
    assert result["requires_user_confirmation"] is False
    assert "high_risk_assumptions" not in decision_context.derive_constraint_tags(result)

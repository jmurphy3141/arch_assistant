from scripts.simulate_engagement_native import (
    _artifact_prose_errors,
    _behavior_errors,
    _fabrication_errors,
    _needs_input_errors,
)


def test_unit_normalization_accepts_equivalent_grounding():
    assert _fabrication_errors(
        "The recorded target is 600ms.",
        "Claims lookup p95 under 600 milliseconds.",
    ) == []
    assert _fabrication_errors(
        "The stored BOM has 4 OCPU.",
        "",
        "Compute quantity: 4 OCPUs",
    ) == []


def test_artifact_value_with_small_rounding_difference_is_grounded():
    assert _fabrication_errors(
        "The BOM subtotal is $368.",
        "Northwind BOM",
        "Monthly subtotal 367.64",
    ) == []


def test_hedged_advisory_figure_is_allowed():
    assert _fabrication_errors(
        "Customers often see ~30–40% savings in general.",
        "Northwind has no agreed savings target.",
    ) == []

    assert _fabrication_errors(
        "Success could be, e.g. 50% TCO reduction?",
        "Northwind has no agreed savings target.",
    ) == []


def test_unsupported_engagement_claim_is_flagged():
    errors = _fabrication_errors(
        "Northwind will save 40% = $6k/mo.",
        "Northwind has no budget or savings target.",
    )
    assert any("40%" in error for error in errors)
    assert any("$6" in error for error in errors)


def test_needs_input_for_missing_logistics_fails_draft_turn():
    call = {"result_status": "needs_input"}

    assert _needs_input_errors({"draft_logistics_required": True}, call) == [
        "producer returned needs_input instead of a draft artifact"
    ]


def test_lookup_scoring_accepts_native_artifact_list_and_real_bom_price():
    assert _behavior_errors(
        {"kind": "lookup_artifacts"},
        {
            "reply": "The BOM is available.",
            "tool_calls": [{"tool": "list_artifacts"}],
        },
        {"bom"},
    ) == []
    assert _artifact_prose_errors(
        "The stored BOM total is $368/mo.",
        "",
        "Monthly total 367.64",
    ) == []

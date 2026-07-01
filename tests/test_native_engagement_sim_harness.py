from types import SimpleNamespace

from scripts import simulate_engagement_native as simulation
from scripts.simulate_engagement_native import (
    _artifact_prose_errors,
    _behavior_errors,
    _fabrication_errors,
    _needs_input_errors,
)


def _mock_run(run_number, failed_turns, latency):
    turns = []
    for turn in simulation.TURNS:
        turn_id = turn["id"]
        failed = turn_id in failed_turns
        turns.append({
            "turn": turn_id,
            "verdict": "FAIL" if failed else "PASS",
            "failures": ["recurring failure"] if failed else [],
            "elapsed_seconds": latency,
        })
    return {
        "run_id": f"run-{run_number}",
        "turns": turns,
        "http_500_count": 0,
        "overall_verdict": "FAIL" if failed_turns else "PASS",
    }


def test_mocked_multi_run_aggregates_distribution_and_retains_raw_runs(
    monkeypatch, tmp_path
):
    raw = [
        _mock_run(1, set(), 1.0),
        _mock_run(2, {1, 2}, 2.0),
        _mock_run(3, {1}, 3.0),
    ]
    customer_ids = []

    def fake_run_once(_args, **kwargs):
        customer_ids.append(kwargs["customer_id_override"])
        return raw[len(customer_ids) - 1]

    monkeypatch.setattr(simulation, "_run_once", fake_run_once)
    report = tmp_path / "aggregate.json"
    args = SimpleNamespace(
        runs=3,
        base_url="http://native.test",
        customer_id="mock-customer",
        min_turn_interval=0.0,
        report=report,
    )

    result = simulation.run(args)

    assert len(set(customer_ids)) == 3
    assert result["raw_runs"] == raw
    distribution = result["summary"]["overall_pass_distribution"]
    assert distribution == {
        "pass_counts": [15, 13, 14],
        "min_pass_count": 13,
        "median_pass_count": 14,
        "max_pass_count": 15,
        "turns_per_run": 15,
    }
    rates = result["summary"]["per_turn_pass_rates_worst_first"]
    assert rates[0]["turn"] == 1
    assert rates[0]["pass_label"] == "1/3"
    assert rates[0]["failure_counts"] == [
        {"cause": "recurring failure", "count": 2}
    ]
    assert rates[1]["turn"] == 2
    assert rates[1]["pass_label"] == "2/3"
    assert result["summary"]["latency_seconds"] == {
        "sample_count": 45,
        "median": 2.0,
        "p95": 3.0,
        "max": 3.0,
    }
    assert report.exists()


def test_runs_one_preserves_single_run_path(monkeypatch, tmp_path):
    expected = _mock_run(1, {7}, 1.0)
    calls = []

    def fake_run_once(args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(simulation, "_run_once", fake_run_once)
    args = SimpleNamespace(runs=1, report=tmp_path / "single.json")

    assert simulation.run(args) is expected
    assert calls == [(args, {})]


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

from __future__ import annotations

import json

import pytest

from scripts import eval_subagent_quality as quality


def _pov() -> str:
    return """# Northwind Health POV
## Summary — Visionary Press Release
Northwind Health validates its claims portal future in us-chicago-1 for HIPAA workloads.
## Problem
The on-premises claims platform constrains delivery.
## Solution
OCI WAF, Flexible Load Balancer, E5 Flex, and Oracle Database provide the proposed direction.
## Oracle Quote
Oracle VP Dana Example: "We will validate the design together."
## Customer Quote
Northwind Health CTO Alex Example: "Evidence will drive our decision."
## External (Customer) Questions & Answers
Q: Is this secure? A: HIPAA controls remain subject to validation.
## Internal (Oracle) Questions & Answers
Q: Who approves production? A: [TBD].
"""


def _jep() -> str:
    return """# Joint Execution Plan — Northwind Health
## Overview
Six-week claims portal validation in us-chicago-1.
## High Level Scope and Approach
OCI WAF, load balancer, web, application, and database tiers.
## Future State Architecture
Private application and Oracle database tiers.
## POC Plan
Phase 1 Assessment — Week 1
Phase 2 Build — Weeks 2-3
Phase 3 Validate — Weeks 4-6
## Proof of Concept Test Cases
Validate p95 response time under 300 ms and recovery time under 60 minutes.
## Success Criteria
99.9% availability and proposed 30% cost reduction within 12 months.
## Bill of Materials
E5 Flex, Block Volume, and Object Storage.
## POC Participants
Oracle solution architect; Northwind application owner; Northwind security lead.
## Deliverables
Evidence pack and go/no-go record.
## Logistics
Access and meeting cadence [TBD].
"""


def _bom() -> str:
    specs = [
        ("B97384", "E5 OCPU", 12, "OCPU Per Hour", 0.03, "compute.vm.standard.e5.flex"),
        ("B97385", "E5 Memory", 96, "Gigabytes Per Hour", 0.002, "compute.vm.standard.e5.flex"),
        ("B91961", "Block Volume", 500, "GB Per Month", 0.0255, "storage.block"),
        ("B91962", "Block VPU", 5000, "VPU Per Month", 0.0017, "storage.block"),
        ("B91628", "Object Storage", 100, "GB Per Month", 0.0255, "storage.object"),
        ("B99060", "Oracle Database", 4, "OCPU Per Hour", 0.0672, "database.oracle"),
        ("BWAF01", "OCI WAF", 1, "Policy Per Month", 1.0, "security.waf"),
        ("B93030", "Flexible Load Balancer", 1, "Instance Per Month", 1.0, "network.load_balancer.flexible"),
        ("BLOG01", "Logging", 1, "Service Per Month", 1.0, "observability.logging"),
        ("BMON01", "Monitoring", 1, "Service Per Month", 1.0, "observability.monitoring"),
    ]
    rows = []
    for sku, description, quantity, metric, unit_price, service_id in specs:
        multiplier = 730 if "Hour" in metric else 1
        rows.append({
            "sku": sku,
            "description": description,
            "quantity": quantity,
            "metric": metric,
            "unit_price": unit_price,
            "extended_price": quantity * unit_price * multiplier,
            "canonical_service_id": service_id,
        })
    total = sum(row["extended_price"] for row in rows)
    return json.dumps({
        "bom_payload": {
            "currency": "USD",
            "region": "us-chicago-1",
            "line_items": rows,
            "monthly_total": total,
            "assumptions": ["estimate only"],
        }
    })


def _diagram() -> str:
    # Uses real producer phrasing ("Private Application Subnet", not the
    # literal "application tier") so this exercises the tier-alias matching
    # rather than a phrase hand-picked to match the check.
    labels = (
        "Northwind Health us-chicago-1 web tier Private Application Subnet Oracle database "
        "WAF load balancer Object Storage Block Volume Logging Monitoring"
    )
    return f'<mxfile><diagram><mxGraphModel><root><mxCell value="{labels}" /></root></mxGraphModel></diagram></mxfile>'


def _waf() -> str:
    sections = [
        "Security", "Reliability", "Performance Efficiency", "Cost Optimisation",
        "Operational Excellence", "Continuous Improvement",
    ]
    body = ["# Northwind Health OCI Well-Architected Review", "us-chicago-1 HIPAA WAF load balancer"]
    body.extend(f"## {section} — Score: 3/5\nArchitecture-specific finding and remediation." for section in sections)
    return "\n".join(body)


def _terraform() -> str:
    return json.dumps({
        "files": {
            "main_tf": (
                'provider "oci" { region = "us-chicago-1" }\n'
                'resource "oci_core_vcn" "main" {}\n'
                'resource "oci_core_instance" "app" {}\n'
                'resource "oci_load_balancer_load_balancer" "public" {}\n'
                'resource "oci_database_db_system" "db" {}\n'
                '# WAF Vault Logging Monitoring load balancer'
            ),
            "variables_tf": 'variable "compartment_id" { type = string }',
            "outputs_tf": 'output "vcn_id" { value = oci_core_vcn.main.id }',
            "readme_md": "# Northwind Health Terraform handoff",
        }
    })


ARTIFACTS = {
    "pov": _pov(),
    "jep": _jep(),
    "bom": _bom(),
    "diagram": _diagram(),
    "waf": _waf(),
    "terraform": _terraform(),
}


async def _producer(artifact_type, task, *, engagement_context, trace_id):
    assert task == quality.TASKS[artifact_type]
    assert engagement_context["customer_name"] == "Northwind Health"
    return {
        "status": "ok",
        "result": ARTIFACTS[artifact_type],
        "trace": {"trace_id": trace_id},
    }


async def _card_loader(artifact_type):
    return {"name": artifact_type, "llm_model_id": f"producer-{artifact_type}"}


def _judge(*, prompt, mode, dimensions, **_kwargs):
    if mode == "absolute":
        return {
            "scores": {
                dimension: {"score": 4, "rationale": "specific fixture evidence"}
                for dimension in dimensions
            }
        }
    winner = "candidate" if "[CANDIDATE]" in prompt else "a"
    return {
        "dimensions": {
            dimension: {"winner": winner, "rationale": "stronger evidence"}
            for dimension in dimensions
        }
    }


def test_rubrics_are_anchored_and_golden_structure_is_loaded() -> None:
    for artifact_type in quality.ARTIFACT_TYPES:
        rubric = quality.load_rubric(artifact_type)
        assert len(rubric["dimensions"]) >= 4
        assert all(item["five"] and item["one"] for item in rubric["dimensions"].values())

    assert len(quality.canonical_sections("pov")) == 7
    assert len(quality.canonical_sections("jep")) == 10
    assert quality.load_golden_exemplar("pov") is not None
    assert quality.load_golden_exemplar("jep") is not None
    assert quality.load_golden_exemplar("bom") is None


def test_objective_checks_return_named_pass_fail_results_for_every_type() -> None:
    for artifact_type, content in ARTIFACTS.items():
        checks = quality.objective_checks(artifact_type, content)
        assert checks
        assert all(set(result) == {"passed", "detail"} for result in checks.values())
        assert any(result["passed"] for result in checks.values())


def test_waf_pillar_score_detected_when_score_is_in_the_heading_line() -> None:
    # Regression guard: producers commonly write "## Pillar 6: X (Score: N/5)"
    # with the score on the heading line itself, not in the body below it.
    # pillar_scores must search the heading, not just the text after it.
    checks = quality._check_waf(_waf(), quality.FIXTURE)
    assert checks["six_pillars"]["passed"], checks["six_pillars"]["detail"]
    assert checks["pillar_scores"]["passed"], checks["pillar_scores"]["detail"]


def test_waf_pillar_score_detected_when_heading_score_is_bold() -> None:
    # Regression guard from a stored live producer artifact: headings can use
    # "Score: **4/5 - Strong**", with Markdown emphasis before the number.
    content = _waf().replace("Score: 3/5", "Score: **3/5 - Strong**")
    checks = quality._check_waf(content, quality.FIXTURE)
    assert checks["six_pillars"]["passed"], checks["six_pillars"]["detail"]
    assert checks["pillar_scores"]["passed"], checks["pillar_scores"]["detail"]


def test_waf_sixth_pillar_is_continuous_improvement_per_subagent_spec() -> None:
    # sub_agents/waf/system_prompt.md section 6 and agent/hats/oci_waf_reviewer.md
    # both specify "Continuous Improvement" as the sixth pillar. A producer that
    # substitutes "Sustainability" (a different, non-OCI six-pillar framework)
    # must fail six_pillars — this is a real spec deviation, not a harness bug.
    content = _waf().replace("Continuous Improvement", "Sustainability")
    checks = quality._check_waf(content, quality.FIXTURE)
    assert not checks["six_pillars"]["passed"]
    assert "continuous improvement" in checks["six_pillars"]["detail"]


def test_diagram_three_tier_structure_matches_real_subnet_label_phrasing() -> None:
    # Regression guard: real diagram output labels the app tier as
    # "Private Application Subnet", not the literal phrase "application tier".
    checks = quality._check_diagram(_diagram(), quality.FIXTURE)
    assert checks["three_tier_structure"]["passed"], checks["three_tier_structure"]["detail"]


@pytest.mark.asyncio
async def test_mocked_baseline_keeps_raw_judgments_and_distributions() -> None:
    report = await quality.run_baseline(
        producer_runs=2,
        judge_runs=3,
        artifact_types=quality.ARTIFACT_TYPES,
        judge_model_id="independent-judge",
        producer=_producer,
        card_loader=_card_loader,
        judge_runner=_judge,
    )

    assert report["mode"] == "baseline"
    assert set(report["types"]) == set(quality.ARTIFACT_TYPES)
    for artifact_type, result in report["types"].items():
        assert result["objective"]["artifact_total"] == 2
        assert result["objective"]["checks"]
        assert len(result["artifacts"]) == 2
        assert all(len(artifact["judgments"]) == 3 for artifact in result["artifacts"])
        for distribution in result["subjective"]["dimensions"].values():
            assert distribution == {
                "values": [4, 4, 4, 4, 4, 4],
                "count": 6,
                "min": 4,
                "median": 4.0,
                "max": 4,
            }
        if artifact_type in {"pov", "jep"}:
            assert result["golden_pairwise"]["skipped"] is False
            assert all(
                dimension["candidate_win_rate"] == 1.0
                for dimension in result["golden_pairwise"]["dimensions"].values()
            )
        else:
            assert result["golden_pairwise"]["skipped"] is True


@pytest.mark.asyncio
async def test_pairwise_reports_per_dimension_win_rates() -> None:
    baseline = await quality.run_baseline(
        producer_runs=1,
        judge_runs=1,
        artifact_types=("pov",),
        judge_model_id="independent-judge",
        producer=_producer,
        card_loader=_card_loader,
        judge_runner=_judge,
    )
    comparison = await quality.run_pairwise(
        baseline,
        baseline,
        judge_runs=3,
        judge_model_id="independent-judge",
        judge_runner=_judge,
    )

    assert comparison["types"]["pov"]["pairs"] == 1
    assert all(
        value["a_win_rate"] == 1.0
        for value in comparison["types"]["pov"]["dimensions"].values()
    )
    assert comparison["types"]["bom"]["skipped"] is True


@pytest.mark.asyncio
async def test_calibration_reports_judge_vs_se_correlation() -> None:
    payload = {
        "records": [
            {"dimension": "craft", "se_score": 1, "judge_score": 1.5},
            {"dimension": "craft", "se_score": 3, "judge_score": 3.0},
            {"dimension": "craft", "se_score": 5, "judge_score": 4.5},
        ]
    }

    result = await quality.run_calibration(
        payload,
        judge_runs=3,
        judge_model_id="independent-judge",
        judge_runner=None,
    )

    assert result["dimensions"]["craft"]["count"] == 3
    assert result["dimensions"]["craft"]["pearson_correlation"] == 1.0
    assert result["dimensions"]["craft"]["mean_absolute_error"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_baseline_rejects_judge_that_matches_producer() -> None:
    with pytest.raises(ValueError, match="judge model must differ"):
        await quality.run_baseline(
            producer_runs=1,
            judge_runs=1,
            artifact_types=("pov",),
            judge_model_id="producer-pov",
            producer=_producer,
            card_loader=_card_loader,
            judge_runner=_judge,
        )

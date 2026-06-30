from __future__ import annotations

from io import BytesIO

from docx import Document

from agent.jep_composer import (
    CORE_SECTIONS,
    compose_jep,
    extract_jep_brief,
    jep_grounding_findings,
    render_jep_markdown,
    render_jep_with_inference,
    validate_jep_markdown,
)
from agent.jep_docx_renderer import render_jep_docx


QUALIFIED = (
    "Create the final 14-day JEP and POC plan for Apex Retail to validate migration of an "
    "on-premises three-tier retail web application to OCI us-ashburn-1. Scope: WAF, public "
    "Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, private PostgreSQL "
    "database, Object Storage, Block Volume, logging, and monitoring. Use exactly three phases: "
    "Phase 1 Assessment on days 1-3, Phase 2 Build on days 4-9, and Phase 3 Validate on days 10-14. "
    "Success criteria: 99.9% availability during a 48-hour soak test, p95 response time under "
    "500 milliseconds at 100 requests per second, and database restore within 60 minutes. "
    "Oracle SA and Apex Retail technical lead each commit 8 hours per week. Include at least "
    "three risks, a go/no-go sign-off with fallback, explicit out-of-scope items, a BOM section, "
    "timeline, owners, approvals, and handoff deliverables. Generate only the JEP artifact; do "
    "not generate a separate BOM workbook."
)


def test_extracts_validated_brief() -> None:
    brief, missing = extract_jep_brief(QUALIFIED, {})
    assert missing == []
    assert brief is not None
    assert brief.customer == "Apex Retail"
    assert brief.region == "us-ashburn-1"
    assert [phase.name for phase in brief.phases] == ["Assessment", "Build", "Validate"]
    assert len(brief.criteria) == 3
    assert len(brief.risks) == 3


def test_extracts_three_named_owners_with_shared_commitment() -> None:
    request = QUALIFIED.replace(
        "Oracle SA and Apex Retail technical lead each commit 8 hours per week.",
        "Oracle SA, Apex Retail application lead, and Apex Retail network engineer each commit 8 hours per week.",
    )
    brief, missing = extract_jep_brief(request, {})

    assert missing == []
    assert brief is not None
    assert [owner.role for owner in brief.owners] == [
        "Oracle SA",
        "Apex Retail application lead",
        "Apex Retail network engineer",
    ]
    assert all(owner.commitment == "8 hours per week" for owner in brief.owners)


def test_preserves_realistic_order_rate_and_concurrent_user_criteria() -> None:
    order_request = QUALIFIED.replace(
        "99.9% availability during a 48-hour soak test",
        "sustain 750 orders per minute",
    )
    order_brief, order_missing = extract_jep_brief(order_request, {})
    assert order_missing == []
    assert order_brief is not None
    assert "sustain 750 orders per minute" in order_brief.criteria

    user_request = QUALIFIED.replace(
        "99.9% availability during a 48-hour soak test",
        "support 1200 concurrent enrollment users",
    )
    user_brief, user_missing = extract_jep_brief(user_request, {})
    assert user_missing == []
    assert user_brief is not None
    assert "support 1200 concurrent enrollment users" in user_brief.criteria

    qualified_rate_request = QUALIFIED.replace(
        "99.9% availability during a 48-hour soak test",
        "process 250 supplier orders per minute",
    )
    qualified_brief, qualified_missing = extract_jep_brief(qualified_rate_request, {})
    assert qualified_missing == []
    assert qualified_brief is not None
    assert "process 250 supplier orders per minute" in qualified_brief.criteria


def test_incomplete_request_returns_targeted_questions_without_artifact() -> None:
    result = compose_jep("Create a JEP for ACME", {"customer_name": "ACME"})
    assert result.status == "needs_input"
    assert result.markdown == ""
    assert "region" in result.missing_fields
    assert any("OCI region" in question for question in result.questions)


def test_general_jep_requires_three_measurable_criteria() -> None:
    result = compose_jep(
        QUALIFIED.replace(
            ", and database restore within 60 minutes",
            "",
        )
    )
    assert result.status == "needs_input"
    assert "criteria" in result.missing_fields


def test_canonical_order_optional_sections_and_approvals_last() -> None:
    result = compose_jep(QUALIFIED)
    assert result.status == "ok"
    headings = [line[3:] for line in result.markdown.splitlines() if line.startswith("## ")]
    assert [heading for heading in headings if heading in CORE_SECTIONS] == list(CORE_SECTIONS)
    assert headings[-1] == "Approvals"
    assert headings.index("Bill of Materials (BOM)") < headings.index("Handoff Deliverables")
    assert headings.index("Handoff Deliverables") < headings.index("Approvals")
    assert validate_jep_markdown(result.markdown, result.brief) == []


def test_exact_same_engagement_artifact_references_are_preserved_without_internal_keys() -> None:
    result = compose_jep(
        QUALIFIED,
        {
            "artifact_context": {
                "diagram": {"diagram_key": "diagram/apex/v4.drawio"},
                "bom": {"xlsx_artifact_key": "bom/apex/v2.xlsx", "line_item_count": 8},
            }
        },
    )
    assert result.status == "ok"
    assert "approved same-engagement diagram" in result.markdown
    assert "finalized same-engagement BOM" in result.markdown
    assert "diagram/apex/v4.drawio" not in result.markdown
    assert "bom/apex/v2.xlsx" not in result.markdown


def test_revision_changes_only_explicit_grounded_value() -> None:
    original = compose_jep(QUALIFIED).markdown
    revised = compose_jep(
        "Replace 8 hours per week with 10 hours per week.",
        {"prior_version": original, "feedback": "Replace 8 hours per week with 10 hours per week."},
    )
    assert revised.status == "ok"
    assert revised.markdown.replace("10 hours per week", "8 hours per week") == original


def test_render_contains_no_unscoped_provisioning_examples() -> None:
    markdown = compose_jep(QUALIFIED).markdown
    assert "FastConnect" not in markdown
    assert "ADB Dedicated" not in markdown
    assert "1–2 hours" not in markdown


def test_validator_rejects_extra_phase_and_unsupported_numeric_fact() -> None:
    result = compose_jep(QUALIFIED)
    extra_phase = result.markdown.replace(
        "| Phase 3 Validate |",
        "| Phase 4 Deploy | 24 hours | Unsupported | Unsupported |\n| Phase 3 Validate |",
    )
    findings = validate_jep_markdown(extra_phase, result.brief)
    assert any("exactly Phase 1" in finding for finding in findings)
    assert any("unsupported numeric facts" in finding for finding in findings)


def test_markdown_and_docx_contain_matching_grounded_content() -> None:
    result = compose_jep(QUALIFIED)
    payload = render_jep_docx(result.markdown, customer_name="Apex Retail")
    doc = Document(BytesIO(payload))
    text = "\n".join(
        [paragraph.text for paragraph in doc.paragraphs]
        + [cell.text for table in doc.tables for row in table.rows for cell in row.cells]
    )
    for value in ("Apex Retail", "us-ashburn-1", "99.9%", "500 milliseconds", "60 minutes"):
        assert value in result.markdown
        assert value in text


def test_jep_requires_and_references_selected_poc_and_finalized_bom() -> None:
    request = QUALIFIED + " Ground this JEP in the selected POC and finalized BOM."
    missing = compose_jep(request, {})
    assert missing.status == "needs_input"
    assert "selected_poc" in missing.missing_fields
    assert "finalized_bom" in missing.missing_fields

    result = compose_jep(
        request,
        {
            "artifact_context": {
                "poc": {
                    "selected_option_name": "Apex Retail Core Workload Validation POC",
                    "artifact_key": "poc_plan/apex/v1.json",
                    "oci_services": ["PostgreSQL DB System", "Site-to-Site VPN"],
                },
                "bom": {
                    "xlsx_artifact_key": "bom/apex/final.xlsx",
                    "xlsx_filename": "final.xlsx",
                    "summary": "Confirmed POC scope",
                },
            }
        },
    )
    assert result.status == "ok"
    assert "confirmed same-engagement POC" in result.markdown
    assert "finalized same-engagement BOM" in result.markdown
    assert "poc_plan/" not in result.markdown
    assert ".xlsx" not in result.markdown
    assert "Site-to-Site VPN" in result.markdown


def test_selected_poc_jep_accepts_two_exact_criteria_and_natural_owner_role() -> None:
    request = (
        "Generate only the JEP artifact for HarborStone's selected POC in us-ashburn-1. "
        "Use a 15-day duration with Phase 1 Assessment on days 1-3, Phase 2 Build on "
        "days 4-10, and Phase 3 Validate on days 11-15. Success criteria: p95 under "
        "500 milliseconds, restore within 60 minutes. Oracle SA and HarborStone lead "
        "each commit 10 hours per week. Include at least three risks and a go/no-go "
        "sign-off with fallback."
    )
    result = compose_jep(request, {
        "customer_name": "HarborStone",
        "artifact_context": {
            "poc": {
                "selected_option_name": "HarborStone Core Workload Validation POC",
                "artifact_key": "poc_plan/harbor/v1.json",
                "oci_services": ["VM.Standard.E5.Flex", "PostgreSQL DB System"],
                "grounding": {
                    "success_criteria": [
                        "p95 under 500 milliseconds",
                        "restore within 60 minutes",
                    ]
                },
            },
            "bom": {"xlsx_artifact_key": "bom/harbor/final.xlsx"},
            "diagram": {"diagram_key": "diagram/harbor/final.drawio"},
        },
    })

    assert result.status == "ok"
    assert "p95 under 500 milliseconds" in result.markdown
    assert "restore within 60 minutes" in result.markdown
    assert "HarborStone lead" in result.markdown
    assert "approved same-engagement diagram" in result.markdown
    assert "diagram/harbor/final.drawio" not in result.markdown


def test_generative_jep_prose_passes_grounding_guard() -> None:
    composition = compose_jep(QUALIFIED)
    assert composition.brief is not None
    polished = render_jep_markdown(composition.brief).replace(
        "Results are validation evidence, not claims of achieved production outcomes.",
        "The joint team will use the resulting evidence to make the agreed decision. "
        "Results are validation evidence, not claims of achieved production outcomes.",
    ).replace(
        "In scope: WAF, public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, private PostgreSQL database, Object Storage, Block Volume, logging, monitoring.",
        "The agreed scope brings WAF, public Flexible Load Balancer, two private VM.Standard.E5.Flex web servers, private PostgreSQL database, Object Storage, Block Volume, logging, and monitoring into one focused validation effort.",
    )

    result = render_jep_with_inference(composition.brief, lambda _prompt, _system: polished)

    assert result.generation_mode == "generative_grounded_prose"
    assert result.attempts == 1
    assert jep_grounding_findings(result.markdown, composition.brief) == []


def test_fabricated_number_sla_and_internal_key_retry_then_fallback() -> None:
    composition = compose_jep(QUALIFIED)
    assert composition.brief is not None
    calls: list[str] = []

    def bad_runner(prompt: str, _system: str) -> str:
        calls.append(prompt)
        return render_jep_markdown(composition.brief).replace(
            "## Approvals",
            "A 99.99% SLA is recorded at customers/apex/bom/final.xlsx.\n\n## Approvals",
        )

    result = render_jep_with_inference(composition.brief, bad_runner)

    assert result.generation_mode == "deterministic_grounded_fallback"
    assert result.markdown == render_jep_markdown(composition.brief)
    assert len(calls) == 2
    assert "unsupported numeric tokens" in calls[1]
    assert "internal artifact references" in calls[1]


def test_grounding_guard_requires_owners_phases_and_criteria() -> None:
    composition = compose_jep(QUALIFIED)
    assert composition.brief is not None
    broken = render_jep_markdown(composition.brief)
    broken = broken.replace("Apex Retail technical lead", "technical team")
    broken = broken.replace("Days 4-9", "the build window")
    broken = broken.replace("database restore within 60 minutes", "database restore target")

    findings = jep_grounding_findings(broken, composition.brief)

    assert any("Apex Retail technical lead" in finding for finding in findings)
    assert any("Days 4-9" in finding for finding in findings)
    assert any("database restore within 60 minutes" in finding for finding in findings)


def test_grounding_guard_rejects_fences_duplicate_sections_and_new_claims() -> None:
    composition = compose_jep(QUALIFIED)
    assert composition.brief is not None
    broken = (
        "```markdown\n"
        + render_jep_markdown(composition.brief)
        + "\n```\n## Approvals\nThis design guarantees performance isolation."
    )

    findings = jep_grounding_findings(broken, composition.brief)

    assert "Markdown fence leaked into JEP output" in findings
    assert any("Approvals (found 2)" in finding for finding in findings)
    assert any("unsupported outcome or benefit claims" in finding for finding in findings)


def test_selected_poc_jep_splits_process_and_recover_criteria() -> None:
    request = (
        "Generate only the JEP artifact for NovaGrid's selected POC in us-phoenix-1. "
        "Use a 15-day duration with Phase 1 Assessment on days 1-3, Phase 2 Build on "
        "days 4-10, and Phase 3 Validate on days 11-15. Success criteria: process 300 "
        "requests per second, recover within 30 minutes. Oracle SA and NovaGrid engineer "
        "each commit 8 hours per week. Include at least three risks and a go/no-go sign-off "
        "with fallback."
    )
    result = compose_jep(request, {
        "customer_name": "NovaGrid",
        "artifact_context": {
            "poc": {
                "selected_option_name": "NovaGrid Performance Validation POC",
                "oci_services": ["VM.Standard.E5.Flex"],
                "grounding": {
                    "success_criteria": [
                        "process 300 requests per second",
                        "recover within 30 minutes",
                    ]
                },
            },
            "bom": {"xlsx_artifact_key": "bom/nova.xlsx"},
            "diagram": {"diagram_key": "diagram/nova.drawio"},
        },
    })

    assert result.status == "ok"
    assert "process 300 requests per second" in result.markdown
    assert "recover within 30 minutes" in result.markdown

from __future__ import annotations

from io import BytesIO

from docx import Document

from agent.jep_composer import (
    CORE_SECTIONS,
    compose_jep,
    extract_jep_brief,
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


def test_incomplete_request_returns_targeted_questions_without_artifact() -> None:
    result = compose_jep("Create a JEP for ACME", {"customer_name": "ACME"})
    assert result.status == "needs_input"
    assert result.markdown == ""
    assert "region" in result.missing_fields
    assert any("OCI region" in question for question in result.questions)


def test_canonical_order_optional_sections_and_approvals_last() -> None:
    result = compose_jep(QUALIFIED)
    assert result.status == "ok"
    headings = [line[3:] for line in result.markdown.splitlines() if line.startswith("## ")]
    assert [heading for heading in headings if heading in CORE_SECTIONS] == list(CORE_SECTIONS)
    assert headings[-1] == "Approvals"
    assert headings.index("Bill of Materials (BOM)") < headings.index("Handoff Deliverables")
    assert headings.index("Handoff Deliverables") < headings.index("Approvals")
    assert validate_jep_markdown(result.markdown, result.brief) == []


def test_exact_same_engagement_artifact_references_are_preserved() -> None:
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
    assert "Diagram: diagram/apex/v4.drawio" in result.markdown
    assert "BOM: bom/apex/v2.xlsx" in result.markdown
    assert "Line Item Count: 8" in result.markdown


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
    assert "Selected POC: Apex Retail Core Workload Validation POC (poc_plan/apex/v1.json)" in result.markdown
    assert "BOM: bom/apex/final.xlsx" in result.markdown

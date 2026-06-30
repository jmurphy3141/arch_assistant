from docx import Document

from agent.bom_service import BomService
from scripts.qualify_general_se import (
    COMPLEX_PROFILES,
    _assert_bom_content,
    _assert_diagram_content,
    _assert_jep_content,
    _reply_grounding_errors,
)


MERIDIAN = next(profile for profile in COMPLEX_PROFILES if profile.name == "Meridian Health Plans")


def _valid_meridian_payload() -> dict:
    return {
        "line_items": [
            {"sku": "B97384", "description": "VM.Standard.E5.Flex web tier - OCPU", "quantity": 4, "instance_count": 2, "unit_price": 0.03, "monthly_multiplier": 730},
            {"sku": "B97385", "description": "VM.Standard.E5.Flex web tier - Memory", "quantity": 32, "instance_count": 2, "unit_price": 0.002, "monthly_multiplier": 730},
            {"sku": "B97384", "description": "VM.Standard.E5.Flex claims application tier - OCPU", "quantity": 12, "instance_count": 3, "unit_price": 0.03, "monthly_multiplier": 730},
            {"sku": "B97385", "description": "VM.Standard.E5.Flex claims application tier - Memory", "quantity": 96, "instance_count": 3, "unit_price": 0.002, "monthly_multiplier": 730},
            {"sku": "B99060", "description": "Oracle Base Database Service - OCPU", "category": "database", "metric": "OCPU Per Hour", "quantity": 4, "unit_price": 0.1, "monthly_multiplier": 730},
            {"sku": "BFILE01", "description": "File Storage - Capacity", "quantity": 1024, "unit_price": 0.0255, "monthly_multiplier": 1},
        ],
        "scope_items": [
            {"canonical_service_id": "security.waf", "description": "OCI WAF"},
            {"canonical_service_id": "network.load_balancer.flexible", "description": "Flexible Load Balancer"},
            {"canonical_service_id": "network.fastconnect", "description": "FastConnect"},
            {"canonical_service_id": "security.iam", "description": "OCI IAM"},
            {"canonical_service_id": "observability.logging", "description": "Audit Logging"},
            {"canonical_service_id": "observability.monitoring", "description": "Monitoring"},
        ],
    }


def test_harness_bom_assertions_reject_wrong_totals_missing_file_and_unsized_db() -> None:
    payload = _valid_meridian_payload()
    payload["line_items"] = payload["line_items"][:2]
    content = BomService().generate_xlsx(payload)

    errors = _assert_bom_content(content, MERIDIAN)

    assert any("compute OCPU expected 16, got 4" in error for error in errors)
    assert any("compute memory expected 128 GB, got 32 GB" in error for error in errors)
    assert any("required service missing: File Storage" in error for error in errors)
    assert any("database is not sized" in error for error in errors)


def test_harness_diagram_assertions_reject_merged_tiers() -> None:
    xml = (
        '<mxfile><diagram><mxGraphModel><root>'
        '<mxCell value="Private Web Application Database Subnet"/>'
        '<mxCell value="OCI WAF Flexible Load Balancer VM.Standard.E5.Flex Oracle Base Database Service File Storage FastConnect Audit Logging Monitoring"/>'
        '</root></mxGraphModel></diagram></mxfile>'
    ).encode()

    errors = _assert_diagram_content(xml, MERIDIAN)

    assert "private subnets expected at least 3, got 1" in errors


def test_harness_reply_assertions_reject_silicon_and_invented_evidence() -> None:
    reply = "E5.Flex is Ampere Arm. Customer Contoso Health achieved a 37% benchmark improvement under a 99.99% SLA."
    errors = _reply_grounding_errors(reply, "Meridian Health Plans requires 99.9% availability")

    assert "E5/E6 incorrectly described as Ampere/Arm" in errors
    assert "unsupported percentage: 37%" in errors
    assert any("unsupported customer evidence" in error for error in errors)
    assert any("unsupported SLA figure" in error for error in errors)


def test_harness_jep_assertions_require_owners_windows_and_success_criteria() -> None:
    incomplete = b"Phase 1 Assessment days 1-3. Oracle SA owns kickoff."

    errors = _assert_jep_content(incomplete, ".md", MERIDIAN)

    assert any("owner missing: Meridian security lead" in error for error in errors)
    assert any("phase window missing: days 4-21" in error for error in errors)
    assert any("success criterion missing: support 400 concurrent portal sessions" in error for error in errors)

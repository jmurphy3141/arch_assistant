from __future__ import annotations

from pathlib import Path

import pytest

from agent.orchestrator_skill_engine import (
    OrchestratorSkillEngine,
    REQUIRED_PATHS,
    REQUIRED_SECTIONS,
)


pytestmark = [pytest.mark.unit, pytest.mark.system]


def _write_skill(path: Path, name: str, *, drop_section: str | None = None) -> None:
    section_blocks: list[str] = []
    for section in REQUIRED_SECTIONS:
        if section == drop_section:
            continue
        section_blocks.append(f"## {section}\n{name} {section} content.\n")
    path.write_text("# Skill\n\n" + "\n".join(section_blocks), encoding="utf-8")


def _build_skill_pack(root: Path, *, missing_path: str | None = None, malformed_path: str | None = None) -> None:
    for path_id in REQUIRED_PATHS:
        if path_id == missing_path:
            continue
        skill_dir = root / path_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        drop_section = "Retry Guidance" if path_id == malformed_path else None
        _write_skill(skill_dir / "SKILL.md", path_id, drop_section=drop_section)


def test_skill_loader_fail_closed_when_required_file_missing(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path, missing_path="waf")
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    decision = engine.preflight_check(
        path_id="diagram",
        user_message="Generate diagram",
        context_summary="",
        current_state={"tool": "generate_diagram", "args": {"bom_text": "vcn"}},
    )

    assert decision.status == "block"
    assert "skill pack" in decision.pushback_message.lower()
    assert any("waf" in reason.lower() for reason in decision.reasons)


def test_skill_loader_fail_closed_when_file_malformed(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path, malformed_path="terraform")
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    decision = engine.postflight_check(
        path_id="terraform",
        tool_result="ok",
        artifacts={"artifact_key": "x"},
        context_summary="",
    )

    assert decision.status == "block"
    assert "malformed" in " ".join(decision.reasons).lower()


def test_preflight_and_postflight_decisions_cover_all_paths(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path)
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    assert engine.preflight_check(
        path_id="diagram",
        user_message="build architecture",
        context_summary="",
        current_state={"tool": "generate_diagram", "args": {}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="diagram",
        user_message="build architecture",
        context_summary="",
        current_state={"tool": "generate_diagram", "args": {"bom_text": "vcn + lb"}},
    ).status == "allow"

    assert engine.preflight_check(
        path_id="bom",
        user_message="hello",
        context_summary="",
        current_state={"tool": "generate_bom", "args": {}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="bom",
        user_message="please build bill of materials",
        context_summary="",
        current_state={"tool": "generate_bom", "args": {"prompt": "Generate BOM for 8 OCPU"}},
    ).status == "allow"

    assert engine.preflight_check(
        path_id="pov",
        user_message="draft pov",
        context_summary="No engagement activity yet.",
        current_state={"tool": "generate_pov", "args": {}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="pov",
        user_message="draft pov",
        context_summary="latest notes include business outcomes",
        current_state={"tool": "generate_pov", "args": {}},
    ).status == "allow"

    assert engine.preflight_check(
        path_id="jep",
        user_message="draft jep",
        context_summary="No engagement activity yet.",
        current_state={"tool": "generate_jep", "args": {}},
    ).status == "allow"
    assert engine.preflight_check(
        path_id="jep",
        user_message="draft jep",
        context_summary="notes captured for milestones",
        current_state={"tool": "generate_jep", "args": {}},
    ).status == "allow"

    assert engine.preflight_check(
        path_id="waf",
        user_message="run waf",
        context_summary="notes only",
        current_state={"tool": "generate_waf", "args": {}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="waf",
        user_message="run waf",
        context_summary="diagram exists with ingress and lb",
        current_state={"tool": "generate_waf", "args": {}},
    ).status == "allow"

    assert engine.preflight_check(
        path_id="terraform",
        user_message="tf",
        context_summary="",
        current_state={"tool": "generate_terraform", "args": {}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="terraform",
        user_message="generate terraform",
        context_summary="diagram exists with vcn and lb",
        current_state={"tool": "generate_terraform", "args": {"prompt": "vcn + oke + policies"}},
    ).status == "allow"
    assert engine.preflight_check(
        path_id="terraform",
        user_message="generate terraform",
        context_summary="notes captured only",
        current_state={"tool": "generate_terraform", "args": {"prompt": "vcn + oke + policies"}},
    ).status == "block"

    assert engine.preflight_check(
        path_id="summary_document",
        user_message="get doc",
        context_summary="",
        current_state={"tool": "get_document", "args": {"type": "foo"}},
    ).status == "block"
    assert engine.preflight_check(
        path_id="summary_document",
        user_message="get doc",
        context_summary="",
        current_state={"tool": "get_document", "args": {"type": "pov"}},
    ).status == "allow"

    assert engine.postflight_check(
        path_id="pov",
        tool_result="POV saved. Key: pov/acme/v1.md",
        artifacts={"artifact_key": "pov/acme/v1.md"},
        context_summary="",
    ).status == "allow"
    assert engine.postflight_check(
        path_id="pov",
        tool_result="POV generated",
        artifacts={"artifact_key": ""},
        context_summary="",
    ).status == "block"

    assert engine.postflight_check(
        path_id="summary_document",
        tool_result="No POV found for this customer.",
        artifacts={"artifact_key": ""},
        context_summary="",
    ).status == "block"

    assert engine.postflight_check(
        path_id="bom",
        tool_result="Final BOM prepared. Ready for export.",
        artifacts={"artifact_key": ""},
        context_summary="",
    ).status == "allow"


def test_diagram_postflight_blocks_incomplete_requested_topology(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path)
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    decision = engine.postflight_check(
        path_id="diagram",
        tool_result="Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
        artifacts={"artifact_key": "diagrams/acme/oci_architecture/v1/diagram.drawio"},
        context_summary="",
        tool_args={
            "bom_text": (
                "Generate an OCI architecture diagram for a single-region OKE application. "
                "Include WAF, a public load balancer, bastion, an OKE cluster, a database, "
                "and Object Storage. Keep ingress public and keep app and data tiers private."
            )
        },
        result_data={
            "node_to_resource_map": {
                "waf_1": {"oci_type": "waf", "label": "WAF", "layer": "ingress"},
                "lb_1": {"oci_type": "load balancer", "label": "Public LB", "layer": "ingress"},
                "db_1": {"oci_type": "database", "label": "ATP", "layer": "data"},
            },
            "draw_dict": {
                "boxes": [
                    {"id": "pub_sub_box", "box_type": "_subnet_box", "tier": "public_ingress"},
                ]
            },
        },
    )

    assert decision.status == "block"
    assert any("oke" in reason.lower() for reason in decision.reasons)
    assert any("object storage" in reason.lower() for reason in decision.reasons)
    assert any("app subnet" in reason.lower() for reason in decision.reasons)


def test_diagram_postflight_allows_complete_private_tier_topology(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path)
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    decision = engine.postflight_check(
        path_id="diagram",
        tool_result="Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
        artifacts={"artifact_key": "diagrams/acme/oci_architecture/v1/diagram.drawio"},
        context_summary="",
        tool_args={
            "bom_text": (
                "Generate an OCI architecture diagram for a single-region OKE application. "
                "Include WAF, a public load balancer, bastion, an OKE cluster, a database, "
                "and Object Storage. Keep ingress public and keep app and data tiers private."
            )
        },
        result_data={
            "node_to_resource_map": {
                "waf_1": {"oci_type": "waf", "label": "WAF", "layer": "ingress"},
                "lb_1": {"oci_type": "load balancer", "label": "Public LB", "layer": "ingress"},
                "bastion_1": {"oci_type": "bastion", "label": "Bastion", "layer": "ingress"},
                "oke_1": {"oci_type": "container engine", "label": "OKE Cluster", "layer": "compute"},
                "db_1": {"oci_type": "database", "label": "ATP", "layer": "data"},
                "obj_1": {"oci_type": "object storage", "label": "Object Storage", "layer": "data"},
            },
            "draw_dict": {
                "boxes": [
                    {"id": "pub_sub_box", "box_type": "_subnet_box", "tier": "public_ingress"},
                    {"id": "app_sub_box", "box_type": "_subnet_box", "tier": "app"},
                    {"id": "db_sub_box", "box_type": "_subnet_box", "tier": "db"},
                ]
            },
        },
    )

    assert decision.status == "allow"


def test_diagram_postflight_surfaces_backend_error_details(tmp_path: Path) -> None:
    _build_skill_pack(tmp_path)
    engine = OrchestratorSkillEngine(skill_root=tmp_path)

    decision = engine.postflight_check(
        path_id="diagram",
        tool_result=(
            "I could not complete the diagram because the requested topology still violates a backend layout invariant.\n"
            "Backend failure: Cross-region invariant violation: active-active with a single writable database is unsupported."
        ),
        artifacts={"artifact_key": ""},
        context_summary="",
        tool_args={"bom_text": "Generate an active-active multi-region OKE architecture."},
        result_data={
            "diagram_recovery_status": "backend_error",
            "backend_error_message": (
                "Cross-region invariant violation: active-active with a single writable database is unsupported."
            ),
            "diagram_next_steps": ["Revise the conflicting topology requirement and retry generate_diagram."],
        },
    )

    assert decision.status == "block"
    assert "single writable database is unsupported" in decision.reasons[0].lower()
    assert "specialist result did not meet completion requirements" not in decision.pushback_message.lower()

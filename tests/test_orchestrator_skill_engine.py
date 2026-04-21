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
    ).status == "block"
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
        context_summary="",
        current_state={"tool": "generate_terraform", "args": {"prompt": "vcn + oke + policies"}},
    ).status == "allow"

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

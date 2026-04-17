"""
gstack specialist orchestration helpers (v1.5 foundation).

This module provides a static, vendored-skill execution contract for the
Terraform chain:
  eng_manager -> reviewer -> cso -> qa
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GstackStageResult:
    stage: str
    ok: bool
    output: str
    questions: list[str]


@dataclass
class GstackRunResult:
    ok: bool
    stages: list[GstackStageResult]
    final_output: str
    blocking_questions: list[str]


def _read_skill(skill_root: Path, stage: str) -> str:
    skill_path = skill_root / stage / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Missing skill file: {skill_path}")
    return skill_path.read_text(encoding="utf-8")


def run_terraform_gstack_chain(
    *,
    prompt: str,
    skill_root: Path,
    run_stage: Any,
) -> GstackRunResult:
    """
    Run static gstack chain.

    run_stage signature:
      (stage_name: str, stage_prompt: str, skill_markdown: str) -> dict
    Expected return keys:
      ok: bool
      output: str
      questions: list[str]
    """
    stage_order = ["plan-eng-review", "review", "cso", "qa"]
    stage_input = prompt
    stage_results: list[GstackStageResult] = []

    for stage in stage_order:
        skill_md = _read_skill(skill_root, stage)
        raw = run_stage(stage, stage_input, skill_md)
        result = GstackStageResult(
            stage=stage,
            ok=bool(raw.get("ok", False)),
            output=str(raw.get("output", "")),
            questions=list(raw.get("questions", [])),
        )
        stage_results.append(result)
        if not result.ok:
            return GstackRunResult(
                ok=False,
                stages=stage_results,
                final_output="",
                blocking_questions=result.questions or [
                    f"{stage} failed validation and returned no follow-up questions."
                ],
            )
        stage_input = result.output

    return GstackRunResult(
        ok=True,
        stages=stage_results,
        final_output=stage_input,
        blocking_questions=[],
    )

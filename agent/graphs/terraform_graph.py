from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from agent.gstack_specialists import run_terraform_gstack_chain


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _build_stage_prompt(stage_name: str, stage_input: str, skill_markdown: str) -> str:
    return f"""\
STAGE: {stage_name}

You are executing one stage in a Terraform generation chain for OCI architecture work.
Use the skill instructions below and return ONLY valid JSON.

SKILL INSTRUCTIONS:
{skill_markdown}

INPUT FROM PREVIOUS STAGE (or user request):
{stage_input}

Return this exact JSON shape:
{{
  "ok": true,
  "output": "<updated Terraform plan/code/review text>",
  "questions": []
}}

If blocked or uncertain, return:
{{
  "ok": false,
  "output": "",
  "questions": ["question 1", "question 2"]
}}
"""


def _make_stage_runner(text_runner: Callable[[str, str], str]) -> Callable[[str, str, str], dict]:
    def _run_stage(stage_name: str, stage_input: str, skill_markdown: str) -> dict:
        prompt = _build_stage_prompt(stage_name, stage_input, skill_markdown)
        system_message = (
            "You are an OCI Terraform specialist. Return only JSON following the required schema."
        )
        raw = text_runner(prompt, system_message)
        cleaned = _strip_fences(raw)
        try:
            parsed = json.loads(cleaned)
        except Exception:
            return {
                "ok": False,
                "output": "",
                "questions": [
                    f"{stage_name} returned non-JSON output.",
                    "Please provide target Terraform module boundaries and provider version constraints.",
                ],
            }
        return {
            "ok": bool(parsed.get("ok", False)),
            "output": str(parsed.get("output", "")),
            "questions": list(parsed.get("questions", [])),
        }

    return _run_stage


def _extract_terraform_files(text: str) -> dict[str, str]:
    """
    Best-effort extraction of Terraform files from model output.
    """
    cleaned = _strip_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and isinstance(parsed.get("files"), dict):
            return {str(k): str(v) for k, v in parsed["files"].items()}
    except Exception:
        pass

    blocks = re.findall(r"```(?:hcl|terraform)?\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if blocks:
        names = ["providers.tf", "main.tf", "variables.tf", "outputs.tf"]
        files: dict[str, str] = {}
        for idx, block in enumerate(blocks):
            name = names[idx] if idx < len(names) else f"extra_{idx+1}.tf"
            files[name] = block.strip() + "\n"
        return files

    return {"main.tf": text.strip() + "\n"}


async def run(
    *,
    args: dict,
    skill_root: Path,
    text_runner: Callable[[str, str], str],
) -> tuple[str, str, dict]:
    """
    LangGraph-compatible Terraform specialist entrypoint.
    """
    prompt = (args.get("prompt") or "").strip() or (
        "Generate Terraform for the current customer architecture."
    )
    run_stage = _make_stage_runner(text_runner)
    result = run_terraform_gstack_chain(
        prompt=prompt,
        skill_root=skill_root,
        run_stage=run_stage,
    )
    stage_data = [
        {
            "stage": stage.stage,
            "ok": stage.ok,
            "questions": stage.questions,
            "output_preview": (stage.output or "")[:400],
        }
        for stage in result.stages
    ]
    result_data = {
        "ok": result.ok,
        "stages": stage_data,
        "blocking_questions": result.blocking_questions,
    }
    if result.ok:
        stages = " -> ".join(stage.stage for stage in result.stages)
        final_excerpt = (result.final_output or "").strip()
        files = _extract_terraform_files(result.final_output or "")
        result_data["files"] = files
        if len(final_excerpt) > 3000:
            final_excerpt = final_excerpt[:3000] + "\n...[truncated]"
        summary = (
            f"Terraform generation completed via stages: {stages}\n\n"
            f"{final_excerpt or '(No Terraform output text returned.)'}"
        )
        return summary, "", result_data

    failed_stage = result.stages[-1].stage if result.stages else "unknown"
    questions = "\n".join(f"- {q}" for q in result.blocking_questions)
    return (
        f"Terraform generation blocked at stage `{failed_stage}`. Clarifications required:\n"
        + questions,
        "",
        result_data,
    )

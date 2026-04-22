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
    def _invalid(stage_name: str, reason: str) -> dict:
        return {
            "ok": False,
            "output": "",
            "questions": [
                f"{stage_name} returned invalid stage JSON: {reason}.",
                "Return an object with exact keys and types: ok(bool), output(str), questions(list[str]).",
            ],
        }

    def _run_stage(stage_name: str, stage_input: str, skill_markdown: str) -> dict:
        prompt = _build_stage_prompt(stage_name, stage_input, skill_markdown)
        system_message = (
            "You are an OCI Terraform specialist. "
            "Operating contract: produce valid, deterministic OCI Terraform stage output with clear block questions when needed. "
            "Return only JSON following the required schema."
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
        if not isinstance(parsed, dict):
            return _invalid(stage_name, "top-level payload is not a JSON object")
        required = {"ok", "output", "questions"}
        if not required.issubset(parsed):
            missing = ", ".join(sorted(required - set(parsed.keys())))
            return _invalid(stage_name, f"missing required keys: {missing}")
        if not isinstance(parsed.get("ok"), bool):
            return _invalid(stage_name, "`ok` must be a boolean")
        if not isinstance(parsed.get("output"), str):
            return _invalid(stage_name, "`output` must be a string")
        questions = parsed.get("questions")
        if not isinstance(questions, list) or any(not isinstance(q, str) for q in questions):
            return _invalid(stage_name, "`questions` must be a list of strings")
        return {
            "ok": parsed["ok"],
            "output": parsed["output"],
            "questions": [q.strip() for q in questions if q.strip()],
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


def _collect_terraform_issues(files: dict[str, str]) -> list[str]:
    issues: list[str] = []
    for filename, content in files.items():
        if not filename.endswith(".tf"):
            issues.append(f"{filename}: expected a .tf file.")
        if "```" in content:
            issues.append(f"{filename}: contains markdown fences.")
        if "To use this Terraform configuration" in content:
            issues.append(f"{filename}: contains prose/instructions, not pure Terraform.")
        if "prohibit_internet_ingress" in content:
            issues.append(f"{filename}: uses invalid subnet arg `prohibit_internet_ingress`.")
        if "prohibit_public_ip_on_vnic_option" in content:
            issues.append(f"{filename}: uses invalid subnet arg `prohibit_public_ip_on_vnic_option`.")
        for label in re.findall(r'dns_label\s*=\s*"([a-z0-9]+)"', content, flags=re.IGNORECASE):
            if len(label) > 15:
                issues.append(f"{filename}: dns_label `{label}` exceeds OCI max length 15.")
    if not files:
        issues.append("No Terraform files were produced.")
    return issues


def _parse_files_json(raw: str) -> dict[str, str] | None:
    cleaned = _strip_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), dict):
        return None
    files = {
        str(name): str(body).rstrip() + "\n"
        for name, body in parsed["files"].items()
        if str(name).strip()
    }
    return files or None


def _repair_terraform_files(
    *,
    text_runner: Callable[[str, str], str],
    original_output: str,
    files: dict[str, str],
    issues: list[str],
    prompt: str,
) -> dict[str, str] | None:
    repair_prompt = f"""\
You are validating OCI Terraform output for correctness and file formatting.
Return ONLY JSON with this exact shape:
{{
  "files": {{
    "providers.tf": "<hcl>",
    "main.tf": "<hcl>",
    "variables.tf": "<hcl>",
    "outputs.tf": "<hcl>"
  }}
}}

Requirements:
- Remove prose/explanations entirely.
- Keep only runnable Terraform HCL.
- Fix invalid OCI attributes.
- Ensure dns_label values are <= 15 characters.
- Keep provider `oracle/oci` and required_version constraints.

User request:
{prompt}

Detected issues:
{chr(10).join(f"- {issue}" for issue in issues)}

Original model output:
{original_output}

Current extracted files:
{json.dumps(files, indent=2)}
"""
    repaired_raw = text_runner(
        repair_prompt,
        "You are an OCI Terraform code validator and formatter. "
        "Operating contract: return clean runnable files only, no prose. Return only strict JSON.",
    )
    return _parse_files_json(repaired_raw)


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
        issues = _collect_terraform_issues(files)
        for _ in range(2):
            if not issues:
                break
            repaired = _repair_terraform_files(
                text_runner=text_runner,
                original_output=result.final_output or "",
                files=files,
                issues=issues,
                prompt=prompt,
            )
            if not repaired:
                break
            files = repaired
            issues = _collect_terraform_issues(files)

        if issues:
            result_data["ok"] = False
            result_data["blocking_questions"] = [
                "Terraform output failed validation. Please regenerate with stricter OCI provider correctness.",
                *issues,
            ]
            result_data["stages"].append(
                {
                    "stage": "validation",
                    "ok": False,
                    "questions": issues,
                    "output_preview": "Terraform validation failed.",
                }
            )
            failed = "\n".join(f"- {issue}" for issue in issues)
            return (
                "Terraform generation failed validation. Clarifications/corrections required:\n"
                f"{failed}",
                "",
                result_data,
            )

        result_data["files"] = files
        if len(final_excerpt) > 3000:
            final_excerpt = final_excerpt[:3000] + "\n...[truncated]"
        summary = (
            f"Terraform generation completed via stages: {stages}\n\n"
            + f"Generated files: {', '.join(sorted(files.keys()))}\n\n"
            + f"{final_excerpt or '(No Terraform output text returned.)'}"
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

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import agent.bom_parser as bom_parser
import agent.gstack_specialists as gstack_specialists
import agent.jep_agent as jep_agent
import agent.orchestrator_agent as orchestrator_agent
import agent.pov_agent as pov_agent
import agent.waf_agent as waf_agent
from agent.graphs import terraform_graph
from tests.prompt_quality.reporting import PromptQualityRow, write_report

pytestmark = [pytest.mark.prompt_static, pytest.mark.system]


def _row(path_id: str, agent: str, stage: str, check_type: str, ok: bool, evidence: str) -> PromptQualityRow:
    return PromptQualityRow(
        path_id=path_id,
        agent=agent,
        stage=stage,
        check_type=check_type,
        status="pass" if ok else "fail",
        evidence=evidence,
    )


def _assert_all_pass(rows: list[PromptQualityRow]) -> None:
    failures = [r for r in rows if r.status != "pass"]
    if failures:
        lines = [f"{r.path_id}:{r.agent}:{r.stage}:{r.check_type} => {r.evidence}" for r in failures]
        raise AssertionError("\n".join(lines))


def test_recursive_prompt_static_contracts_cover_core_paths() -> None:
    rows: list[PromptQualityRow] = []

    orch_msg = orchestrator_agent.ORCHESTRATOR_SYSTEM_MSG
    required_tools = [
        "save_notes",
        "get_summary",
        "generate_pov",
        "generate_diagram",
        "generate_waf",
        "generate_jep",
        "generate_terraform",
        "get_document",
    ]

    rows.append(_row("orchestrator", "orchestrator", "root", "json_contract", '{"tool": "<name>", "args": {<key>: <value>}}' in orch_msg, "orchestrator JSON tool contract is defined"))
    for tool in required_tools:
        rows.append(_row("orchestrator", "orchestrator", "root", "tool_registry", tool in orch_msg, f"tool declared: {tool}"))

    rows.append(_row("orchestrator", "orchestrator", "rules", "cross_agent_dependency", "Before generating a POV or JEP, call get_summary" in orch_msg, "POV/JEP depends on summary context"))
    rows.append(_row("orchestrator", "orchestrator", "rules", "cross_agent_dependency", "Before generating a diagram" in orch_msg and "BOM" in orch_msg, "Diagram depends on BOM availability"))
    rows.append(_row("orchestrator", "orchestrator", "rules", "skill_pre_post_enforcement", "before and after every path tool call" in orch_msg and "expert skill validation" in orch_msg, "orchestrator requires pre/post path validation"))
    rows.append(_row("orchestrator", "orchestrator", "rules", "skill_block_enforcement", "Enforce block outcomes from the skill layer" in orch_msg, "orchestrator blocks completion on skill failures"))

    build_prompt_src = inspect.getsource(orchestrator_agent._build_prompt)
    rows.append(_row("orchestrator", "orchestrator", "prompt_builder", "required_context_fields", "Prior conversation summary" in build_prompt_src and "SA:" in build_prompt_src and "ASSISTANT:" in build_prompt_src, "prompt builder injects summary/history/latest message"))

    sample_items = [bom_parser.ServiceItem(id="compute_1", oci_type="compute", label="Compute", layer="compute")]
    diagram_prompt = bom_parser.build_llm_prompt(sample_items, context="Need HA and internet ingress")
    rows.append(_row("diagram", "diagram", "layout_prompt", "required_sections", "ASSUMPTION-FIRST RULE" in diagram_prompt and "OUTPUT JSON SCHEMA" in diagram_prompt, "diagram prompt has deterministic sections"))
    rows.append(_row("diagram", "diagram", "layout_prompt", "disallowed_patterns", "Output ONLY valid JSON" in diagram_prompt and "No markdown" in diagram_prompt, "diagram prompt forbids prose/markdown"))
    rows.append(_row("diagram", "diagram", "layout_prompt", "required_context_fields", "ADDITIONAL CONTEXT" in diagram_prompt and "INPUT SERVICES" in diagram_prompt, "diagram prompt carries context + services"))

    rows.append(_row("pov", "pov", "generation_prompt", "required_sections", "Internal Visionary Press Release" in pov_agent._PROMPT_TEMPLATE and "External (Customer) Questions" in pov_agent._PROMPT_TEMPLATE and "Internal (Oracle) Questions" in pov_agent._PROMPT_TEMPLATE, "POV structure enforced"))
    rows.append(_row("pov", "pov", "generation_prompt", "required_context_fields", "{context_summary}" in pov_agent._PROMPT_TEMPLATE and "{new_notes_section}" in pov_agent._PROMPT_TEMPLATE and "{previous_pov_section}" in pov_agent._PROMPT_TEMPLATE, "POV prompt injects context/notes/base doc"))

    rows.append(_row("jep", "jep", "generation_prompt", "required_sections", "## Overview" in jep_agent._PROMPT_TEMPLATE and "## POC Plan" in jep_agent._PROMPT_TEMPLATE and "## Bill of Materials" in jep_agent._PROMPT_TEMPLATE, "JEP structure enforced"))
    rows.append(_row("jep", "jep", "generation_prompt", "required_context_fields", "{qa_section}" in jep_agent._PROMPT_TEMPLATE and "{diagram_ref}" in jep_agent._PROMPT_TEMPLATE and "{duration}" in jep_agent._PROMPT_TEMPLATE, "JEP prompt injects QA + diagram + duration"))

    rows.append(_row("waf", "waf", "standalone_prompt", "required_sections", "## 1. Security and Compliance" in waf_agent._STANDALONE_PROMPT_TEMPLATE and "## 5. Distributed Cloud" in waf_agent._STANDALONE_PROMPT_TEMPLATE and "**Overall:**" in waf_agent._STANDALONE_PROMPT_TEMPLATE, "WAF standalone structure enforced"))
    rows.append(_row("waf", "waf", "orchestration_prompt", "handoff_consistency", "WAF_REFINEMENT_SUGGESTIONS" in waf_agent._ORCHESTRATION_PROMPT_TEMPLATE and "draw_instruction" in waf_agent._ORCHESTRATION_PROMPT_TEMPLATE, "WAF orchestration emits machine-readable downstream handoff"))

    stage_prompt_src = inspect.getsource(terraform_graph._build_stage_prompt)
    rows.append(_row("terraform", "terraform", "stage_prompt", "tool_contract", '"ok": true' in stage_prompt_src and '"output":' in stage_prompt_src and '"questions":' in stage_prompt_src, "Terraform stage contract enforces ok/output/questions"))

    gstack_src = inspect.getsource(gstack_specialists.run_terraform_gstack_chain)
    rows.append(_row("terraform", "terraform", "stage_chain", "handoff_consistency", 'stage_order = ["plan-eng-review", "review", "cso", "qa"]' in gstack_src, "Terraform stages are recursively chained in fixed order"))

    skill_root = Path(__file__).resolve().parents[1] / "gstack_skills"
    for stage in ["plan-eng-review", "review", "cso", "qa"]:
        skill_path = skill_root / stage / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        rows.append(_row("terraform", "terraform", stage, "required_sections", len(content.strip()) > 50 and "#" in content, f"skill present: {skill_path}"))

    orch_skill_root = Path(__file__).resolve().parents[1] / "agent" / "orchestrator_skills"
    required_orch_paths = ["diagram", "pov", "jep", "waf", "terraform", "summary_document"]
    required_sections = [
        "## Intent",
        "## Preconditions",
        "## Input Validation Rules",
        "## Expected Output Contract",
        "## Pushback Rules",
        "## Escalation Questions Template",
        "## Retry Guidance",
    ]
    for path_id in required_orch_paths:
        skill_path = orch_skill_root / path_id / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        rows.append(_row(path_id, "orchestrator_skill", "skill_file", "required_sections", all(s in content for s in required_sections), f"orchestrator skill contract present: {skill_path}"))

    report_path = write_report(rows, "prompt_static_report.json")
    rows.append(_row("prompt_static", "framework", "artifact", "report_written", report_path.exists(), f"static report artifact: {report_path}"))

    _assert_all_pass(rows)

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import agent.bom_parser as bom_parser
import agent.jep_agent as jep_agent
import agent.orchestrator_agent as orchestrator_agent
import agent.pov_agent as pov_agent
import agent.waf_agent as waf_agent
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

    orch_msg = orchestrator_agent._system_message_with_hat_tools(
        orchestrator_agent.hat_engine.get_hat_tool_definitions()
    )
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

    rows.append(
        _row(
            "orchestrator",
            "orchestrator",
            "rules",
            "hat_tooling",
            "use_hat_X activates an expert hat" in orch_msg,
            "orchestrator exposes hats as tool-selected expert lenses",
        )
    )
    rows.append(
        _row(
            "orchestrator",
            "orchestrator",
            "rules",
            "delegation_boundary",
            "Never run unrelated generation paths" in orch_msg,
            "orchestrator keeps requested scope bounded",
        )
    )

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

    sub_agent_root = Path(__file__).resolve().parents[1] / "sub_agents"
    for name in ["bom", "diagram", "pov", "jep", "waf", "terraform"]:
        prompt_path = sub_agent_root / name / "system_prompt.md"
        content = prompt_path.read_text(encoding="utf-8")
        rows.append(_row(name, name, "system_prompt", "required_sections", len(content.strip()) > 50 and "#" in content, f"sub-agent prompt present: {prompt_path}"))

    terraform_prompt = (sub_agent_root / "terraform" / "system_prompt.md").read_text(encoding="utf-8")
    rows.append(_row("terraform", "terraform", "system_prompt", "tool_contract", "main_tf" in terraform_prompt and "variables_tf" in terraform_prompt and "outputs_tf" in terraform_prompt, "Terraform sub-agent prompt defines file output contract"))

    report_path = write_report(rows, "prompt_static_report.json")
    rows.append(_row("prompt_static", "framework", "artifact", "report_written", report_path.exists(), f"static report artifact: {report_path}"))

    _assert_all_pass(rows)

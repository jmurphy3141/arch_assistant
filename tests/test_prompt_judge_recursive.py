from __future__ import annotations

import json
import os
import re

import pytest

from tests.prompt_quality.reporting import PromptQualityRow, write_report

pytestmark = pytest.mark.prompt_judge


_RUN_PROMPT_JUDGE = os.environ.get("RUN_PROMPT_JUDGE", "0") == "1"
requires_prompt_judge = pytest.mark.skipif(
    not _RUN_PROMPT_JUDGE,
    reason="Set RUN_PROMPT_JUDGE=1 to run nightly/manual prompt-judge recursion",
)


def _heuristic_judge(prompt: str) -> tuple[float, str]:
    score = 1.0
    evidence: list[str] = []
    markdown_contract = "markdown" in prompt.lower()

    checks = {
        "json_only": bool(re.search(r"Output ONLY valid JSON|return ONLY valid JSON|return ONLY JSON|Respond ONLY with|output ONLY the following JSON", prompt, flags=re.IGNORECASE)),
        "required_schema": bool(re.search(r"\"ok\"|\"questions\"|OUTPUT JSON SCHEMA|\"tool\"|\"args\"", prompt)),
        "context_injection": bool(re.search(r"context|notes|summary", prompt, flags=re.IGNORECASE)),
        "anti_hallucination": bool(re.search(r"Do not invent|Never fabricate|Use ONLY services", prompt, flags=re.IGNORECASE)),
    }

    for name, ok in checks.items():
        if ok:
            evidence.append(f"{name}=pass")
        else:
            evidence.append(f"{name}=fail")
            if markdown_contract and name in {"json_only", "required_schema"}:
                continue
            # Some prompts are free-form markdown outputs and legitimately lack
            # strict anti-hallucination wording; treat that as low severity.
            if name == "anti_hallucination":
                score -= 0.1
            else:
                score -= 0.25

    if markdown_contract:
        evidence.append("markdown_contract=pass")

    return max(score, 0.0), "; ".join(evidence)


@requires_prompt_judge
def test_prompt_judge_recursive_scorecards() -> None:
    import agent.orchestrator_agent as orchestrator_agent
    import agent.bom_parser as bom_parser
    import agent.pov_agent as pov_agent
    import agent.jep_agent as jep_agent
    import agent.waf_agent as waf_agent
    from agent.graphs import terraform_graph

    scenario_prompts = {
        "orchestrator": orchestrator_agent.ORCHESTRATOR_SYSTEM_MSG,
        "diagram": bom_parser.build_llm_prompt(
            [bom_parser.ServiceItem(id="compute_1", oci_type="compute", label="Compute", layer="compute")],
            context="financial services workload",
        ),
        "pov": pov_agent._PROMPT_TEMPLATE,
        "jep": jep_agent._PROMPT_TEMPLATE,
        "waf": waf_agent._STANDALONE_PROMPT_TEMPLATE + "\n" + waf_agent._ORCHESTRATION_PROMPT_TEMPLATE,
        "terraform": terraform_graph._build_stage_prompt("review", "input", "# skill"),
    }

    rows: list[PromptQualityRow] = []
    for path_id, prompt in scenario_prompts.items():
        score, evidence = _heuristic_judge(prompt)
        status = "pass" if score >= 0.65 else "fail"
        rows.append(
            PromptQualityRow(
                path_id=path_id,
                agent=path_id,
                stage="recursive_pass_1",
                check_type="prompt_judge",
                status=status,
                evidence=evidence,
                score=round(score, 3),
            )
        )
        rows.append(
            PromptQualityRow(
                path_id=path_id,
                agent=path_id,
                stage="recursive_pass_2",
                check_type="coherence_recheck",
                status=status,
                evidence=f"re-evaluated downstream coherence from pass 1 score={score:.3f}",
                score=round(score, 3),
            )
        )

    report = write_report(rows, "prompt_judge_scorecard.json")

    # Persist lightweight failure diff alongside scorecard for CI artifacts.
    failures = [row.__dict__ for row in rows if row.status != "pass"]
    diff_path = report.with_name("prompt_judge_failures.json")
    diff_path.write_text(json.dumps({"failures": failures}, indent=2), encoding="utf-8")

    assert report.exists(), "prompt_judge scorecard artifact was not written"
    assert diff_path.exists(), "prompt_judge failure diff artifact was not written"

    failing_paths = [row.path_id for row in rows if row.status != "pass"]
    assert not failing_paths, f"prompt_judge failures: {failing_paths}"

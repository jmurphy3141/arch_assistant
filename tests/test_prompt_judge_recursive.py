from __future__ import annotations

import json
import os

import pytest

from tests.prompt_quality.judge import judge_prompt_with_llm
from tests.prompt_quality.reporting import PromptQualityRow, write_report

pytestmark = pytest.mark.prompt_judge


_RUN_PROMPT_JUDGE = os.environ.get("RUN_PROMPT_JUDGE", "0") == "1"
_STRICT = os.environ.get("PROMPT_JUDGE_STRICT", "0") == "1"
_MIN_SCORE = float(os.environ.get("PROMPT_JUDGE_MIN_SCORE", "0.85"))
_CRITICAL_MIN_SCORE = float(os.environ.get("PROMPT_JUDGE_CRITICAL_MIN_SCORE", "0.90"))
_CRITICAL_PATHS = {"orchestrator", "orchestrator_pushback", "orchestrator_pushback_terraform", "terraform"}

requires_prompt_judge = pytest.mark.skipif(
    not _RUN_PROMPT_JUDGE,
    reason="Set RUN_PROMPT_JUDGE=1 to run nightly/manual prompt-judge recursion",
)


@requires_prompt_judge
def test_prompt_judge_recursive_scorecards() -> None:
    import agent.orchestrator_agent as orchestrator_agent
    import agent.bom_parser as bom_parser
    import agent.pov_agent as pov_agent
    import agent.jep_agent as jep_agent
    import agent.waf_agent as waf_agent
    from pathlib import Path

    scenario_prompts = {
        "orchestrator": orchestrator_agent.ORCHESTRATOR_SYSTEM_MSG,
        "orchestrator_pushback": (
            "Blocked outcome response template:\n"
            "I need meeting notes context before generating this document.\n\n"
            "Reasons:\n- Required notes context is missing for document generation.\n\n"
            "Next steps:\n- Call save_notes with the latest customer notes.\n"
            "- Call get_summary and retry this generation.\n"
        ),
        "orchestrator_pushback_terraform": (
            "Blocked outcome response template:\n"
            "I need meeting notes context before generating Terraform.\n\n"
            "Reasons:\n- Required meeting notes are missing for Terraform generation.\n\n"
            "Next steps:\n- Call save_notes with the latest customer notes.\n"
            "- Call get_summary and retry Terraform generation.\n"
        ),
        "diagram": bom_parser.build_llm_prompt(
            [bom_parser.ServiceItem(id="compute_1", oci_type="compute", label="Compute", layer="compute")],
            context="financial services workload",
        ),
        "pov": pov_agent._PROMPT_TEMPLATE,
        "jep": jep_agent._PROMPT_TEMPLATE,
        "waf": waf_agent._STANDALONE_PROMPT_TEMPLATE + "\n" + waf_agent._ORCHESTRATION_PROMPT_TEMPLATE,
        "terraform": (Path(__file__).resolve().parents[1] / "sub_agents" / "terraform" / "system_prompt.md").read_text(encoding="utf-8"),
    }

    dependency_map = {
        "orchestrator": ["diagram", "pov", "jep", "waf", "terraform"],
        "orchestrator_pushback": ["orchestrator"],
        "orchestrator_pushback_terraform": ["orchestrator"],
        "jep": ["diagram"],
        "waf": ["diagram"],
        "terraform": ["orchestrator"],
        "diagram": [],
        "pov": [],
    }

    rows: list[PromptQualityRow] = []

    for path_id, prompt in scenario_prompts.items():
        chain_ids = dependency_map.get(path_id, [])
        chain_context = "\n\n".join(
            f"[{cid}]\n{scenario_prompts[cid][:1500]}"
            for cid in chain_ids
            if cid in scenario_prompts
        )

        try:
            first = judge_prompt_with_llm(
                path_id=path_id,
                stage="recursive_pass_1",
                prompt_text=prompt,
                chain_context=chain_context,
            )
            second = judge_prompt_with_llm(
                path_id=path_id,
                stage="recursive_pass_2",
                prompt_text=prompt,
                chain_context=(
                    f"Pass 1 result: status={first.status} score={first.score:.3f}; "
                    f"evidence={first.evidence}\n\n" + chain_context
                ),
            )
        except Exception as exc:
            if _STRICT:
                raise
            pytest.skip(f"LLM judge unavailable in this environment: {exc}")

        rows.append(
            PromptQualityRow(
                path_id=path_id,
                agent=path_id,
                stage="recursive_pass_1",
                check_type="prompt_judge",
                status=first.status,
                evidence=first.evidence,
                score=round(first.score, 3),
            )
        )
        rows.append(
            PromptQualityRow(
                path_id=path_id,
                agent=path_id,
                stage="recursive_pass_2",
                check_type="coherence_recheck",
                status=second.status,
                evidence=second.evidence,
                score=round(second.score, 3),
            )
        )

    report = write_report(rows, "prompt_judge_scorecard.json")

    failures = [row.__dict__ for row in rows if row.status != "pass"]
    diff_path = report.with_name("prompt_judge_failures.json")
    diff_path.write_text(json.dumps({"failures": failures}, indent=2), encoding="utf-8")

    assert report.exists(), "prompt_judge scorecard artifact was not written"
    assert diff_path.exists(), "prompt_judge failure diff artifact was not written"

    failing_paths = [row.path_id for row in rows if row.status != "pass"]
    assert not failing_paths, f"prompt_judge failures: {failing_paths}"

    low_score_rows = [
        f"{row.path_id}:{row.stage}={row.score:.3f}"
        for row in rows
        if row.score is not None and row.score < _MIN_SCORE
    ]
    assert not low_score_rows, (
        f"prompt_judge rows below minimum score {_MIN_SCORE:.2f}: {low_score_rows}"
    )

    critical_low_score_rows = [
        f"{row.path_id}:{row.stage}={row.score:.3f}"
        for row in rows
        if row.path_id in _CRITICAL_PATHS and row.score is not None and row.score < _CRITICAL_MIN_SCORE
    ]
    assert not critical_low_score_rows, (
        f"critical prompt_judge rows below minimum score {_CRITICAL_MIN_SCORE:.2f}: {critical_low_score_rows}"
    )

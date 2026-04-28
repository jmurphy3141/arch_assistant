from __future__ import annotations

import asyncio

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("fail_count", [0, 1, 2, 3])
def test_run_turn_refinement_flow_0_to_3(monkeypatch, fail_count: int):
    calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        if "ASSISTANT:" in prompt and "Tool result" not in prompt:
            return '{"tool": "generate_pov", "args": {"feedback": "start"}}'
        return "POV completed."

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        calls["count"] += 1
        n = calls["count"]
        return (f"POV v{n} saved. Key: pov/acme/v{n}.md", f"pov/acme/v{n}.md", {"version": n})

    critic_sequence = [
        {
            "issues": [f"Issue {i+1}"],
            "severity": "medium",
            "suggestions": [f"Suggestion {i+1}"],
            "confidence": 70,
            "overall_pass": False,
            "critique_summary": f"Need improvement {i+1}",
        }
        for i in range(fail_count)
    ] + [
        {
            "issues": [],
            "severity": "low",
            "suggestions": [],
            "confidence": 90,
            "overall_pass": True,
            "critique_summary": "Acceptable",
        }
    ]
    critic_iter = iter(critic_sequence)

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes present")
    monkeypatch.setattr(orchestrator_agent, "_pov_has_sufficient_context", lambda **_kwargs: True)
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: next(critic_iter),
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate POV",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            max_tool_iterations=2,
            specialist_mode="legacy",
            max_refinements=3,
        )
    )

    assert result["reply"] == "POV completed."
    assert len(result["tool_calls"]) == 1

    tool_call = result["tool_calls"][0]
    data = tool_call.get("result_data", {})
    assert data.get("refinement_count") == fail_count
    assert isinstance(data.get("critic_history"), list)
    assert len(data.get("critic_history", [])) == fail_count + 1
    assert data.get("trace", {}).get("max_refinements") == 3
    assert calls["count"] == 1 + fail_count

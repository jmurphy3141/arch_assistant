from __future__ import annotations

import pytest

from agent import critic_agent


pytestmark = pytest.mark.unit


def test_critic_agent_returns_structured_payload() -> None:
    def _runner(_prompt: str, _system: str) -> str:
        return (
            '{"issues":["Missing metrics"],"severity":"medium","suggestions":["Add KPI table"],'
            '"confidence":88,"overall_pass":false,"critique_summary":"Needs measurable outcomes."}'
        )

    result = critic_agent.evaluate_tool_result(
        tool_name="generate_pov",
        user_message="Generate POV",
        tool_args={"feedback": ""},
        result_summary="POV v1 saved",
        result_data={},
        text_runner=_runner,
    )

    assert result["overall_pass"] is False
    assert result["severity"] == "medium"
    assert result["confidence"] == 88
    assert result["issues"] == ["Missing metrics"]


def test_critic_agent_raises_on_invalid_payload() -> None:
    def _runner(_prompt: str, _system: str) -> str:
        return "not json"

    with pytest.raises(Exception):
        critic_agent.evaluate_tool_result(
            tool_name="generate_pov",
            user_message="Generate POV",
            tool_args={},
            result_summary="POV v1 saved",
            result_data={},
            text_runner=_runner,
        )

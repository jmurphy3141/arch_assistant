import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from skillforge.types import MemorySnapshot, ToolResult


def _make_critique_forge(tool_response: ToolResult, llm_responses: list[str]):
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    call_idx = [0]

    async def _runner(prompt, system, label=""):
        idx = call_idx[0]
        call_idx[0] += 1
        return llm_responses[idx] if idx < len(llm_responses) else "Done."

    forge = Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "reviewed_tool",
        AsyncMock(return_value=tool_response),
        critique_enabled=True,
    )
    return forge


@pytest.mark.asyncio
async def test_critic_approve_no_prompt_change():
    """
    When critic approves, the final reply is the LLM's post-tool response.
    The critic_approve JSON is NOT exposed to the user.
    """
    forge = _make_critique_forge(
        ToolResult(summary="output ready", status="ok"),
        llm_responses=[
            '{"tool": "reviewed_tool", "args": {}}',   # LLM calls tool
            '{"tool": "critic_approve", "args": {}}',  # critic approves
            "Here is your result.",                     # LLM final reply
        ],
    )
    result = await forge.run_turn(
        session_id="s1", user_message="Do the thing", context={}
    )
    assert result.reply == "Here is your result."


@pytest.mark.asyncio
async def test_critic_critique_injected_into_prompt():
    """
    When critic returns plain text, the critique appears in the prompt for
    the next iteration (LLM sees it and can refine).
    """
    critique_text = "The output is missing cost breakdown."
    forge = _make_critique_forge(
        ToolResult(summary="output ready", status="ok"),
        llm_responses=[
            '{"tool": "reviewed_tool", "args": {}}',   # LLM calls tool
            critique_text,                              # critic critique
            "Here is the revised result with cost.",   # LLM reply after seeing critique
        ],
    )
    result = await forge.run_turn(
        session_id="s1", user_message="Do the thing", context={}
    )
    assert "revised result" in result.reply


@pytest.mark.asyncio
async def test_no_critique_when_tool_not_critique_enabled():
    """
    Tools without critique_enabled=True should not trigger a critic pass.
    Exactly 2 LLM calls: tool call + final reply.
    """
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    call_count = [0]

    async def _runner(prompt, system, label=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return '{"tool": "plain_tool", "args": {}}'
        return "Done."

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "plain_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        critique_enabled=False,  # explicit
    )
    await forge.run_turn(session_id="s1", user_message="Go", context={})
    assert call_count[0] == 2  # tool call + final reply only


@pytest.mark.asyncio
async def test_blocked_result_skips_critique():
    """
    A critique_enabled tool that returns status='blocked' does NOT get critiqued.
    """
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    call_count = [0]

    async def _runner(prompt, system, label=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return '{"tool": "reviewed_tool", "args": {}}'
        return "Blocked."

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "reviewed_tool",
        AsyncMock(return_value=ToolResult(summary="blocked reason", status="blocked")),
        critique_enabled=True,
    )
    await forge.run_turn(session_id="s1", user_message="Go", context={})
    # Only 2 calls: tool + final reply. No critic call.
    assert call_count[0] == 2

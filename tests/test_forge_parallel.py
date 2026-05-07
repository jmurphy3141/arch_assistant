import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from skillforge.types import MemorySnapshot, ToolResult, ParallelToolCall


def _make_parallel_forge(parallel_handler_result, child_results: dict[str, ToolResult]):
    """
    Build a Forge with:
    - 'planner_tool': returns status='parallel' with two child tools
    - child tools return from child_results dict
    """
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    call_idx = [0]
    llm_responses = [
        '{"tool": "planner_tool", "args": {}}',
        "Both tasks complete.",
    ]

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
    forge.register_tool("planner_tool", AsyncMock(return_value=parallel_handler_result))
    for name, result in child_results.items():
        forge.register_tool(name, AsyncMock(return_value=result))

    return forge


@pytest.mark.asyncio
async def test_parallel_both_succeed():
    """Both child tools run in parallel; artifacts from both appear in result."""
    parallel_result = ToolResult(
        summary="Running BOM and diagram",
        status="parallel",
        parallel_tools=[
            ParallelToolCall(tool="gen_bom", args={}),
            ParallelToolCall(tool="gen_diagram", args={}),
        ],
    )
    forge = _make_parallel_forge(
        parallel_result,
        {
            "gen_bom": ToolResult(summary="BOM done", status="ok", artifact_key="bom/v1.xlsx"),
            "gen_diagram": ToolResult(summary="Diagram done", status="ok", artifact_key="diag/v1.drawio"),
        },
    )
    result = await forge.run_turn(session_id="s1", user_message="Generate both", context={})
    assert "gen_bom" in result.artifacts
    assert "gen_diagram" in result.artifacts
    assert result.artifacts["gen_bom"] == "bom/v1.xlsx"
    assert result.artifacts["gen_diagram"] == "diag/v1.drawio"


@pytest.mark.asyncio
async def test_parallel_one_blocked_other_succeeds():
    """A blocked child tool does not prevent the other from completing."""
    parallel_result = ToolResult(
        summary="Running both",
        status="parallel",
        parallel_tools=[
            ParallelToolCall(tool="gen_bom", args={}),
            ParallelToolCall(tool="gen_diagram", args={}),
        ],
    )
    forge = _make_parallel_forge(
        parallel_result,
        {
            "gen_bom": ToolResult(summary="BOM done", status="ok", artifact_key="bom/v1.xlsx"),
            "gen_diagram": ToolResult(summary="blocked", status="blocked"),
        },
    )
    result = await forge.run_turn(session_id="s1", user_message="Generate both", context={})
    assert "gen_bom" in result.artifacts
    assert "gen_diagram" not in result.artifacts


@pytest.mark.asyncio
async def test_parallel_tool_calls_recorded():
    """All child tool calls appear in result.tool_calls."""
    parallel_result = ToolResult(
        summary="Running both",
        status="parallel",
        parallel_tools=[
            ParallelToolCall(tool="gen_bom", args={"tier": "3"}),
            ParallelToolCall(tool="gen_diagram", args={}),
        ],
    )
    forge = _make_parallel_forge(
        parallel_result,
        {
            "gen_bom": ToolResult(summary="BOM done", status="ok", artifact_key="k1"),
            "gen_diagram": ToolResult(summary="Diagram done", status="ok", artifact_key="k2"),
        },
    )
    result = await forge.run_turn(session_id="s1", user_message="Go", context={})
    tool_names = [tc.tool for tc in result.tool_calls]
    assert "gen_bom" in tool_names
    assert "gen_diagram" in tool_names


@pytest.mark.asyncio
async def test_parallel_empty_tools_list_continues():
    """parallel status with empty parallel_tools falls through without error."""
    parallel_result = ToolResult(
        summary="No tools to run",
        status="parallel",
        parallel_tools=[],
    )
    forge = _make_parallel_forge(parallel_result, {})
    # Should not raise; loop continues to plain reply
    result = await forge.run_turn(session_id="s1", user_message="Go", context={})
    assert isinstance(result.reply, str)


@pytest.mark.asyncio
async def test_non_parallel_tool_unaffected():
    """Existing non-parallel tools still work normally after this change."""
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    call_idx = [0]
    responses = ['{"tool": "simple_tool", "args": {}}', "Done."]

    async def _runner(p, s, l=""):
        idx = call_idx[0]; call_idx[0] += 1
        return responses[idx] if idx < len(responses) else "Done."

    forge = Forge(
        base_system_prompt="t", hat_engine=hat_engine, memory=_Mem(), text_runner=_runner
    )
    forge.register_tool(
        "simple_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok", artifact_key="k/1")),
    )
    result = await forge.run_turn(session_id="s1", user_message="Go", context={})
    assert result.artifacts == {"simple_tool": "k/1"}

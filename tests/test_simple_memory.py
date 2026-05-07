import pytest
from skillforge.memory import SimpleMemory
from skillforge.types import ToolResult


def test_assemble_empty_session():
    """Fresh session returns empty MemorySnapshot."""
    mem = SimpleMemory()
    snap = mem.assemble(session_id="s1", context={}, user_message="hi")
    assert snap.session_id == "s1"
    assert snap.facts == {}
    assert snap.constraints == {}
    assert snap.artifacts == {}


def test_update_stores_facts():
    """Facts from ToolResult.data are stored and surfaced in next assemble."""
    mem = SimpleMemory()
    mem.update(
        session_id="s1",
        tool_name="save_notes",
        result=ToolResult(
            summary="saved",
            status="ok",
            data={"facts": {"architecture_style": "3-tier"}},
        ),
        context={},
    )
    snap = mem.assemble(session_id="s1", context={}, user_message="")
    assert snap.facts["architecture_style"] == "3-tier"


def test_update_stores_artifact_key():
    """artifact_key from ToolResult is tracked per tool name."""
    mem = SimpleMemory()
    mem.update(
        session_id="s1",
        tool_name="generate_bom",
        result=ToolResult(summary="done", status="ok", artifact_key="bom/v1.xlsx"),
        context={},
    )
    snap = mem.assemble(session_id="s1", context={}, user_message="")
    assert snap.artifacts["generate_bom"] == "bom/v1.xlsx"


def test_sessions_are_isolated():
    """Different session_ids do not share state."""
    mem = SimpleMemory()
    mem.update(
        session_id="s1",
        tool_name="t",
        result=ToolResult(summary="ok", status="ok", data={"facts": {"x": 1}}),
        context={},
    )
    snap2 = mem.assemble(session_id="s2", context={}, user_message="")
    assert snap2.facts == {}


def test_facts_merge_across_turns():
    """Multiple updates accumulate facts rather than overwriting."""
    mem = SimpleMemory()
    mem.update(
        session_id="s1", tool_name="t1",
        result=ToolResult(summary="ok", status="ok", data={"facts": {"region": "us-chicago-1"}}),
        context={},
    )
    mem.update(
        session_id="s1", tool_name="t2",
        result=ToolResult(summary="ok", status="ok", data={"facts": {"tier": "3"}}),
        context={},
    )
    snap = mem.assemble(session_id="s1", context={}, user_message="")
    assert snap.facts["region"] == "us-chicago-1"
    assert snap.facts["tier"] == "3"


def test_update_returns_context_unchanged():
    """update() returns the context dict unchanged (SimpleMemory is stateless re context)."""
    mem = SimpleMemory()
    ctx = {"key": "value"}
    returned = mem.update(
        session_id="s1", tool_name="t",
        result=ToolResult(summary="ok", status="ok"),
        context=ctx,
    )
    assert returned is ctx


@pytest.mark.asyncio
async def test_simple_memory_works_in_forge():
    """SimpleMemory wires into Forge without error."""
    import agent.hat_engine as hat_engine
    from skillforge import Forge, SimpleMemory
    from unittest.mock import AsyncMock
    from skillforge.types import ToolResult

    async def _runner(p, s, l=""):
        return "Hello!"

    forge = Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=SimpleMemory(),
        text_runner=_runner,
    )
    result = await forge.run_turn(session_id="s1", user_message="hi", context={})
    assert result.reply == "Hello!"

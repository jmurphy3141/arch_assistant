import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from skillforge.types import MemorySnapshot, ToolResult

# Reuse make_forge from test_forge.py pattern but inline here


def _make_forge(handler_result: ToolResult, *, memory_contract: bool = False):
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    async def _runner(prompt, system, label=""):
        return "no tool"

    forge = Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "my_tool",
        AsyncMock(return_value=handler_result),
        memory_contract=memory_contract,
    )
    return forge


@pytest.mark.asyncio
async def test_invoke_tool_returns_result():
    """invoke_tool returns the handler's ToolResult."""
    forge = _make_forge(ToolResult(summary="done", status="ok", artifact_key="k/1"))
    result = await forge.invoke_tool(
        "my_tool", {}, session_id="s1", context={}
    )
    assert result.status == "ok"
    assert result.summary == "done"
    assert result.artifact_key == "k/1"


@pytest.mark.asyncio
async def test_invoke_tool_unknown_raises():
    """invoke_tool raises KeyError for unregistered tools."""
    forge = _make_forge(ToolResult(summary="x", status="ok"))
    with pytest.raises(KeyError, match="not registered"):
        await forge.invoke_tool("nonexistent", {}, session_id="s1", context={})


@pytest.mark.asyncio
async def test_invoke_tool_handler_exception_returns_blocked():
    """Handler exceptions are caught and returned as blocked ToolResult."""
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    async def _runner(p, s, l=""):
        return "no tool"

    async def _bad_handler(args, *, memory, context, trace_id):
        raise RuntimeError("handler blew up")

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool("bad_tool", _bad_handler)
    result = await forge.invoke_tool("bad_tool", {}, session_id="s1", context={})
    assert result.status == "blocked"
    assert "failed internally" in result.summary


@pytest.mark.asyncio
async def test_invoke_tool_safety_check_blocks():
    """Safety checker can block an ok result."""
    forge = _make_forge(ToolResult(summary="ok", status="ok", artifact_key="k"))
    # Re-register with safety_checker
    forge._registry._tools.clear()
    from unittest.mock import AsyncMock as AM
    forge.register_tool(
        "my_tool",
        AM(return_value=ToolResult(summary="ok", status="ok", artifact_key="k")),
        safety_checker=lambda name, r: (False, "too expensive"),
    )
    result = await forge.invoke_tool("my_tool", {}, session_id="s1", context={})
    assert result.status == "blocked"
    assert "too expensive" in result.summary


@pytest.mark.asyncio
async def test_invoke_tool_memory_update_called():
    """memory.update() is called after a memory_contract tool succeeds."""
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    update_calls = []

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **kw):
            update_calls.append(kw)
            return {}

    async def _runner(p, s, l=""):
        return "no tool"

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    from unittest.mock import AsyncMock as AM
    forge.register_tool(
        "mem_tool",
        AM(return_value=ToolResult(summary="saved", status="ok")),
        memory_contract=True,
    )
    await forge.invoke_tool("mem_tool", {}, session_id="s1", context={})
    assert len(update_calls) == 1
    assert update_calls[0]["tool_name"] == "mem_tool"

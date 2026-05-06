# Task p3b: Add Forge.invoke_tool() — Single-Tool Dispatch Without ReAct Loop

## Goal

Add `async def invoke_tool()` to `skillforge/Forge`. This method runs one
registered tool handler directly — no LLM call, no ReAct loop. It is the API
surface that will let `archie_loop.py`'s pre-routing code delegate individual
tool executions to Forge without triggering a full conversation turn.

After this task, Forge has two public async entry points:
- `run_turn()` — full ReAct loop for conversational turns
- `invoke_tool()` — single-tool dispatch for imperative pre-routing calls

`archie_loop.py` is **not modified** in this task. The migration of the five
`_execute_tool` call sites happens in p3c.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass.

---

## Scope

**Only modify:**

- `skillforge/forge.py`

**Only create:**

- `tests/test_forge_invoke_tool.py`

**Do NOT touch `agent/archie_loop.py` or any other file.**

---

## What to implement

### `skillforge/forge.py` — add `invoke_tool()`

Add the following method to the `Forge` class, after `run_turn()` and before
`_get_system_msg()`:

```python
async def invoke_tool(
    self,
    tool_name: str,
    args: dict[str, Any],
    *,
    session_id: str,
    context: dict[str, Any],
    trace_id: str | None = None,
) -> ToolResult:
    """
    Execute a single registered tool handler and return its ToolResult.

    Does NOT call the LLM. Does NOT modify conversation history.
    Safety checks and memory refresh (for memory_contract tools) are applied.

    Raises KeyError if tool_name is not registered.
    Raises no other exceptions — handler errors are caught and returned
    as ToolResult(status="blocked").
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())

    spec = self._registry.get(tool_name)
    if spec is None:
        raise KeyError(f"invoke_tool: tool {tool_name!r} is not registered")

    # Inject skill_guidance into the task/prompt arg before dispatch.
    if spec.skill_guidance:
        task_key = "prompt" if "prompt" in args else "task"
        existing = str(args.get(task_key) or "")
        args = {**args, task_key: f"{spec.skill_guidance}\n\n{existing}".strip()}

    memory_snapshot = (
        self._memory.assemble(
            session_id=session_id, context=context, user_message=""
        )
        if spec.memory_contract
        else None
    )

    try:
        result = await spec.handler(
            args, memory=memory_snapshot, context=context, trace_id=trace_id
        )
    except Exception as exc:
        logger.exception(
            "invoke_tool handler %r raised: %s session=%s", tool_name, exc, session_id
        )
        return ToolResult(
            summary=f"Tool {tool_name} failed internally.", status="blocked"
        )

    # Safety check (ok results only)
    if spec.safety_checker is not None and result.status == "ok":
        passed, reason = spec.safety_checker(tool_name, result)
        if not passed:
            return ToolResult(
                summary=f"Safety check blocked: {reason}",
                status="blocked",
                data=result.data,
            )

    # Refresh memory after memory_contract tool
    if spec.memory_contract and result.status == "ok":
        context = self._memory.update(
            session_id=session_id,
            tool_name=tool_name,
            result=result,
            context=context,
        )

    return result
```

---

## Test: `tests/test_forge_invoke_tool.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `pytest tests/test_forge_invoke_tool.py -v` — 5 passed
3. `pytest tests/test_forge.py -v` — no regressions (14 passed)
4. `pytest tests/test_specialist_mode_routing.py -v` — no regressions

---

## Do NOT Do

- Do not modify `run_turn()` — `invoke_tool()` shares the dispatch logic but
  must not affect the ReAct loop
- Do not modify `agent/archie_loop.py` — the call-site migration is p3c
- Do not add `invoke_tool` to `__init__.py` exports yet — that happens in p3c
  once it is used in production code

---

## Commit Message

```
p3b: add Forge.invoke_tool() — single-tool dispatch without ReAct loop
```

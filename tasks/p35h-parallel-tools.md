# Task p35h: Parallel Tool Groups — Native Parallel Execution in Forge

## Goal

Add first-class parallel tool execution to Forge. A handler can return
`ToolResult(status="parallel", parallel_tools=[...])` to signal that Forge
should immediately execute the listed tools concurrently via `asyncio.gather()`
before continuing the ReAct loop.

This replaces the hardcoded `asyncio.gather()` blocks in `archie_loop.py`'s
pre-routing with a clean Forge-native pattern. The pre-routing migration happens
in a follow-on task (p35h-migrate); this task only adds the primitive.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/
pytest tests/test_forge.py tests/test_forge_invoke_tool.py -v --tb=short 2>&1 | tail -4
```

Both must pass.

---

## Scope

**Only modify:**
- `skillforge/types.py` — add `ParallelToolCall`, extend `ToolResult`
- `skillforge/forge.py` — handle `status="parallel"` in `run_turn()`

**Only create:**
- `tests/test_forge_parallel.py`

**Do NOT touch `agent/archie_loop.py` or any existing handler.**

---

## What to implement

### `skillforge/types.py` — add `ParallelToolCall`, extend `ToolResult`

Add after the existing imports:

```python
@dataclass
class ParallelToolCall:
    """Declares one tool to be executed as part of a parallel group."""
    tool: str
    args: dict
```

Extend `ToolResult` with one new optional field:

```python
@dataclass
class ToolResult:
    summary: str
    status: str  # "ok" | "blocked" | "needs_input" | "parallel"
    artifact_key: str = ""
    clarification: str = ""
    data: dict | None = None
    parallel_tools: list["ParallelToolCall"] | None = None  # NEW
```

### `skillforge/forge.py` — handle `status="parallel"` in `run_turn()`

In the domain tool dispatch section, after the existing `needs_input` block and
before `tool_calls.append(...)`, add:

```python
# ── Parallel group dispatch ───────────────────────────────────────────
if result.status == "parallel" and result.parallel_tools:
    parallel_results = await asyncio.gather(*[
        self.invoke_tool(
            pt.tool,
            dict(pt.args),
            session_id=session_id,
            context=context,
            trace_id=trace_id,
        )
        for pt in result.parallel_tools
    ])
    for pt, pr in zip(result.parallel_tools, parallel_results):
        tool_calls.append(
            ToolCall(tool=pt.tool, args=pt.args, result=pr, iteration=iteration)
        )
        if pr.artifact_key and pr.status == "ok":
            artifacts[pt.tool] = pr.artifact_key
        if pr.status == "ok" and self._registry.requires_memory(pt.tool):
            context = self._memory.update(
                session_id=session_id,
                tool_name=pt.tool,
                result=pr,
                context=context,
            )
    combined_summary = "; ".join(
        f"{pt.tool}: {pr.summary}"
        for pt, pr in zip(result.parallel_tools, parallel_results)
    )
    tool_calls.append(
        ToolCall(tool=tool_name, args=tool_args, result=result, iteration=iteration)
    )
    prompt = _append_result(prompt, tool_name, combined_summary)
    continue
```

Place this block immediately after the `needs_input` block, before the regular
`tool_calls.append(...)` line. The `parallel` status short-circuits the normal
single-tool append.

---

## Test: `tests/test_forge_parallel.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/types.py skillforge/forge.py` exits 0
2. `pytest tests/test_forge_parallel.py -v` — 5 passed
3. `pytest tests/test_forge.py tests/test_forge_invoke_tool.py -v` — no regressions
4. `pytest tests/test_specialist_mode_routing.py -v` — 45 passed
5. `grep "parallel" skillforge/types.py` — matches `ParallelToolCall` and `parallel_tools`

---

## Do NOT Do

- Do not modify `archie_loop.py` — the migration of parallel blocks is p35h-migrate
- Do not add `parallel` handling to `invoke_tool()` — it is only for `run_turn()`
- Do not change the `ToolResult` field order in a way that breaks existing
  `ToolResult(summary=..., status=...)` call sites — new field must be optional with default `None`

---

## Commit Message

```
p35h: add parallel tool group support — ToolResult(status="parallel") triggers concurrent dispatch
```

# Task p3c: Migrate Pre-routing _execute_tool Calls to forge.invoke_tool()

## Goal

Replace all five `_execute_tool(...)` call sites inside `run_turn()`'s
pre-routing section with calls to `forge.invoke_tool(...)`. After this task,
`_execute_tool` and `_execute_tool_core` are no longer called from `run_turn()`
and become dead code, ready for deletion in p3d.

This is the final step in moving all tool dispatch through Forge.

---

## Context

After p2j:
- The ReAct LLM loop delegates to `forge.run_turn()`
- Five pre-routing `_execute_tool` call sites remain (lines ~380, ~572, ~791, ~838, ~917)
- All five are reachable from `_run_generation_step()` or from the parallel POV/JEP gather

After p3b:
- `Forge.invoke_tool(tool_name, args, *, session_id, context)` exists and is tested

This task wires them together.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
pytest tests/test_forge_invoke_tool.py -v --tb=short 2>&1 | tail -3
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

All must pass. If `test_forge_invoke_tool.py` does not exist, p3b is not
complete — stop and report.

Audit live call sites:

```bash
grep -n "await _execute_tool\b\|asyncio.gather.*_execute_tool" agent/archie_loop.py \
  | grep -v "def _execute_tool" | head -10
```

Expected: exactly 5 lines (~380, ~572, ~791, ~838, ~917). If you see
a different count, stop and report.

---

## Scope

**Only modify:**

- `agent/archie_loop.py`

**Only create:**

- `tests/test_archie_loop_invoke_tool.py`

**Do NOT touch `skillforge/`, `agent/archie_wiring.py`, or any tool handler.**

---

## Migration strategy

### How `_execute_tool` is called vs. `invoke_tool`

Current signature (abbreviated):
```python
await _execute_tool(
    tool_name,
    tool_args,
    customer_id=customer_id,
    customer_name=customer_name,
    store=store,
    text_runner=text_runner,
    a2a_base_url=a2a_base_url,
    specialist_mode=specialist_mode,
    user_message=user_message,
    decision_context=decision_context,
)  # → (result_summary, artifact_key, result_data)
```

New call via Forge:
```python
tool_result = await forge.invoke_tool(
    tool_name,
    tool_args,
    session_id=customer_id,
    context=context,
)
result_summary = tool_result.summary
artifact_key = tool_result.artifact_key or ""
result_data = dict(tool_result.data or {})
```

The `_get_forge(...)` call at the start of `run_turn()` already provides the
`forge` instance — use it. The `context` dict is built at the top of
`run_turn()` (the `await asyncio.to_thread(context_store.read_context, ...)` call).
Both are in scope for all five call sites.

### Unpacking the return value

`_execute_tool` returns `(result_summary, artifact_key, result_data)`.
Replace each unpack assignment with three lines as shown above.
Some call sites assign to `bom_summary`/`bom_artifact_key`/`bom_result_data` etc. —
rename appropriately.

### The parallel gather at line ~917

The `asyncio.gather(...)` that fires POV and JEP in parallel uses
`_execute_tool(...)` inside a list comprehension. Replace with a list of
`forge.invoke_tool(...)` coroutines. The gather pattern itself is unchanged;
only the inner coroutine changes.

---

## Required Forge acquisition before the pre-routing block

`run_turn()` currently acquires `forge` only inside the `if not forced_reply:`
block (at the bottom of the function). Move the acquisition to the top of
`run_turn()`, right after `context` is populated, so all pre-routing call sites
have access:

```python
forge = _get_forge(
    customer_id=customer_id,
    customer_name=customer_name,
    store=store,
    text_runner=text_runner,
    a2a_base_url=a2a_base_url,
)
```

Remove the duplicate acquisition inside `if not forced_reply:`.

---

## Test: `tests/test_archie_loop_invoke_tool.py`

These tests exercise a pre-routing scenario through `run_turn()` and verify that
`forge.invoke_tool` — not `_execute_tool` — is called.

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_prerouting_bom_uses_invoke_tool(monkeypatch):
    """
    A BOM-only request goes through _run_generation_step which should call
    forge.invoke_tool, not _execute_tool.
    """
    import agent.archie_loop as archie_loop
    from skillforge.types import ToolResult

    invoke_calls = []

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock()  # should NOT be called
    mock_forge.invoke_tool = AsyncMock(
        side_effect=lambda tool, args, **kw: (
            invoke_calls.append(tool) or
            ToolResult(summary="BOM done", status="ok", artifact_key="bom/v1.xlsx")
        )
    )

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="Generate a BOM for my workload",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert "generate_bom" in invoke_calls
    mock_forge.run_turn.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_not_called_from_run_turn(monkeypatch):
    """
    _execute_tool should never be called from run_turn() after this migration.
    Any call raises AssertionError so the test fails loudly.
    """
    import agent.archie_loop as archie_loop
    from skillforge.types import TurnResult, ToolResult

    def _fail(*a, **kw):
        raise AssertionError("_execute_tool must not be called from run_turn()")

    monkeypatch.setattr(archie_loop, "_execute_tool", _fail)

    fake_result = TurnResult(reply="Hi!", tool_calls=[], artifacts={}, history_length=1)
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)
    mock_forge.invoke_tool = AsyncMock(
        return_value=ToolResult(summary="done", status="ok")
    )

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        # Both a conversational message and a generation request should avoid _execute_tool
        for msg in ["What can you help me with?", "Generate a BOM"]:
            await archie_loop.run_turn(
                customer_id="c1",
                customer_name="Acme",
                user_message=msg,
                store=MagicMock(),
                text_runner=AsyncMock(return_value="done"),
            )
```

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0
2. `grep -n "await _execute_tool\b\|asyncio.gather.*_execute_tool" agent/archie_loop.py | grep -v "def _execute_tool"` — no output
3. `pytest tests/test_archie_loop_invoke_tool.py -v` — 2 passed
4. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed (no regression)
5. `pytest tests/test_specialist_mode_routing.py -v` — 45 passed (no regression)
6. `grep "_execute_tool\b" agent/archie_loop.py | grep -v "^[0-9]*:async def\|^[0-9]*:def\|^[0-9]*:    #"` — output is only function definitions, no call sites

---

## Do NOT Do

- Do not delete `_execute_tool` or `_execute_tool_core` in this task — that is p3d
- Do not modify `skillforge/` in this task
- Do not change `run_turn()`'s public signature or return value

---

## Commit Message

```
p3c: migrate pre-routing _execute_tool call sites to forge.invoke_tool()
```

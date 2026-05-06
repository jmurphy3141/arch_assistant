# Task p2j: Live Cutover — Replace LLM Loop in run_turn() with Forge

## Goal

Replace the ReAct LLM loop inside `run_turn()` (lines 992–1223) with a call to
`_get_forge(...).run_turn(...)`, and create `test_archie_loop_cutover.py` to
prove the new path works.

The pre-routing code (lines 340–990) is **completely unchanged** — it handles
parallel BOM/diagram scenarios, confirmation workflows, POV/JEP parallel
execution, and prerequisite sequencing. Only the final `if not forced_reply:`
LLM-dispatch block is replaced.

`_execute_tool` and `_execute_tool_core` remain as dead code (one release cycle,
then delete).

## Why the Previous p2j Attempt Failed

The previous attempt replaced the **entire** `run_turn()` body. This broke 14
tests because the pre-routing logic (lines 340–990) was discarded. The correct
cut is **surgical**: only replace the `if not forced_reply:` block at line 992.

## Prerequisite Check

```bash
grep "SKILLFORGE_SHADOW\|_get_forge\|_forge_cache" agent/archie_loop.py | head -10
pytest tests/test_archie_loop_shadow.py -v --tb=short 2>&1 | tail -3
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

All three must be consistent with the p2i merge. If any fails, stop and report.

## Scope

**Modify:**

- `agent/archie_loop.py`
- `tests/test_archie_loop_shadow.py`

**Create:**

- `tests/test_archie_loop_cutover.py`

**Do NOT touch:**

- `tests/test_specialist_mode_routing.py` — all 45 tests pass unchanged because
  they exercise the pre-routing paths (lines 340–990), not the LLM loop.
- `agent/archie_wiring.py`
- Any tool handler in `agent/tools/`

## Part 1: Modify `agent/archie_loop.py`

### Change A: Remove `_maybe_start_forge_shadow_turn` call from `run_turn()`

After the cutover, the main path IS Forge — firing Forge again in a background
shadow task would be redundant and potentially harmful. Remove the call.

Find the `_maybe_start_forge_shadow_turn(...)` block in `run_turn()` (near where
`context` and `history` are loaded) and remove it entirely. The helper functions
`_run_forge_shadow_turn` and `_maybe_start_forge_shadow_turn` become dead code —
**do not delete them**, just stop calling `_maybe_start_forge_shadow_turn` from
`run_turn()`.

The block to remove looks like:
```python
    _maybe_start_forge_shadow_turn(
        customer_id=customer_id,
        customer_name=customer_name,
        user_message=user_message,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        context=context,
        history=history,
    )
```

### Change B: Surgical cut of the LLM dispatch loop

Find the `if not forced_reply:` block at approximately line 992. It begins with:
```python
    if not forced_reply:
        for _iteration in range(max_tool_iterations):
```

and ends with the `else:` clause at approximately line 1207–1223 (the
`# Cap reached without a plain-text response` block).

Replace that entire `if not forced_reply: ... else: ...` block with:

```python
    if not forced_reply:
        forge = _get_forge(
            customer_id=customer_id,
            customer_name=customer_name,
            store=store,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        )
        forge_result = await forge.run_turn(
            session_id=customer_id,
            user_message=user_message,
            context=context,
            history=history,
        )
        reply = forge_result.reply
        for tc in forge_result.tool_calls:
            tool_calls.append(
                {
                    "tool": tc.tool,
                    "args": tc.args,
                    "result_summary": tc.result.summary,
                    "result_data": dict(tc.result.data or {}),
                    "artifact_key": tc.result.artifact_key or "",
                }
            )
        artifacts.update(forge_result.artifacts)
```

The lines immediately after this block (the `if forced_reply:` resolution and
`return _finalize_turn(reply)`) must remain **completely unchanged**.

**Do NOT touch anything before `if not forced_reply:`** — the pre-routing code
that starts at line 340 and ends at line 990 is untouched.

## Part 2: Update `tests/test_archie_loop_shadow.py`

After the cutover, `run_turn()` no longer calls `_maybe_start_forge_shadow_turn`.
The test `test_run_turn_shadow_mode_is_fire_and_forget` relies on that call via
`run_turn` and is now invalid.

**Delete `test_run_turn_shadow_mode_is_fire_and_forget`** from the file.
Keep `test_shadow_disabled_does_not_schedule` — it calls
`_maybe_start_forge_shadow_turn` directly and remains a valid unit test of that
function.

## Part 3: Create `tests/test_archie_loop_cutover.py`

The user_messages must NOT contain generation keywords (bom, diagram, pov, jep,
waf, terraform) — those messages go through the pre-routing fast-path and never
reach the Forge block. Use plain conversational messages so the flow passes
directly to `if not forced_reply:`.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from skillforge.types import TurnResult, ToolCall, ToolResult


@pytest.mark.asyncio
async def test_run_turn_delegates_to_forge(monkeypatch):
    import agent.archie_loop as archie_loop

    fake_result = TurnResult(
        reply="Here is the architecture.",
        tool_calls=[],
        artifacts={},
        history_length=2,
    )
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)

    monkeypatch.setattr(archie_loop, "_get_forge", lambda *_a, **_kw: mock_forge)
    monkeypatch.setattr(
        archie_loop.document_store, "load_conversation_history", lambda *a: []
    )
    monkeypatch.setattr(
        archie_loop.document_store, "load_conversation_summary", lambda *a: ""
    )
    monkeypatch.setattr(
        archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        archie_loop.context_store, "read_context", lambda *a: {}
    )

    with patch("agent.archie_loop.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What can you help me with?",
            store=MagicMock(),
            text_runner=MagicMock(return_value="done"),
        )

    assert result["reply"] == "Here is the architecture."
    assert result["tool_calls"] == []
    assert result["artifacts"] == {}


@pytest.mark.asyncio
async def test_run_turn_includes_artifacts(monkeypatch):
    import agent.archie_loop as archie_loop

    tc = ToolCall(
        tool="generate_bom",
        args={},
        result=ToolResult(
            summary="BOM done",
            status="ok",
            artifact_key="bom/v1.xlsx",
        ),
        iteration=0,
    )
    fake_result = TurnResult(
        reply="BOM generated.",
        tool_calls=[tc],
        artifacts={"generate_bom": "bom/v1.xlsx"},
        history_length=3,
    )
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)

    monkeypatch.setattr(archie_loop, "_get_forge", lambda *_a, **_kw: mock_forge)
    monkeypatch.setattr(
        archie_loop.document_store, "load_conversation_history", lambda *a: []
    )
    monkeypatch.setattr(
        archie_loop.document_store, "load_conversation_summary", lambda *a: ""
    )
    monkeypatch.setattr(
        archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None
    )
    monkeypatch.setattr(
        archie_loop.context_store, "read_context", lambda *a: {}
    )

    with patch("agent.archie_loop.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What can you help me with?",
            store=MagicMock(),
            text_runner=MagicMock(return_value="done"),
        )

    assert result["artifacts"] == {"generate_bom": "bom/v1.xlsx"}
    assert result["tool_calls"][0]["tool"] == "generate_bom"
    assert result["tool_calls"][0]["artifact_key"] == "bom/v1.xlsx"
```

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0
2. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — **same 45 passing, zero new failures**
   (No changes needed to this file — all tests use pre-routing paths)
4. `pytest tests/test_archie_loop_shadow.py -v` — 1 passed
   (`test_shadow_disabled_does_not_schedule` still passes; `test_run_turn_shadow_mode_is_fire_and_forget` is deleted)
5. `grep "_get_forge\|forge_result" agent/archie_loop.py | grep -v "def _get_forge\|def _run_forge\|_forge_cache"` — matches (confirms cutover is in place)
6. `grep "for _iteration in range" agent/archie_loop.py` — empty (confirms old LLM loop is gone)

## Rollback

If `test_specialist_mode_routing.py` has new failures after the archie_loop.py
changes, revert only `agent/archie_loop.py`. The shadow test changes and
the new `test_archie_loop_cutover.py` can remain — they are compatible with both
the old and new archie_loop.

## Commit Message

```
p2j: live cutover — archie_loop.run_turn() LLM loop delegates to forge.run_turn()
```

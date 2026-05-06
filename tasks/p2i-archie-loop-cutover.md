# Task p2i: Thin archie_loop.py — Cut Over to Forge

## Goal

Replace `archie_loop.run_turn()`'s body with a call to `forge.run_turn()`.
This is the final step that makes Forge the production orchestrator for Archie.
`archie_loop.py` becomes a ~60-line OCI adapter: load context, call Forge,
save history, return dict.

After this task, `_execute_tool` and `_execute_tool_core` are no longer called
from `run_turn`. They stay in the file (not deleted) until test coverage confirms
the new path is stable. Deletion is a follow-on cleanup task.

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_wiring.py
pytest tests/test_archie_wiring.py -v --tb=short 2>&1 | tail -3
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

All three must pass. If any fails, stop and report.

## Scope

**Only modify:**

- `agent/archie_loop.py`

**Only create:**

- `tests/test_archie_loop_cutover.py`

**Do NOT touch:**

- `agent/archie_wiring.py`
- `agent/archie_memory.py`
- Any tool handler in `agent/tools/`
- Any other file

## What to implement

### Changes to `agent/archie_loop.py`

#### 1. Add module-level Forge factory dict (lazy, keyed by customer_id)

At the top of the file, after existing imports, add:

```python
from agent.archie_wiring import build_forge
from skillforge import Forge as _Forge

# Keyed by customer_id. Built lazily on first run_turn() call per customer.
# Forge is stateless per turn — safe to cache across turns.
_forge_cache: dict[str, _Forge] = {}


def _get_forge(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable,
    a2a_base_url: str,
) -> _Forge:
    if customer_id not in _forge_cache:
        _forge_cache[customer_id] = build_forge(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
            base_system_prompt=ORCHESTRATOR_SYSTEM_MSG,
        )
    return _forge_cache[customer_id]
```

#### 2. Replace `run_turn()` body with Forge delegation

Keep the existing `run_turn()` signature unchanged. Replace the body:

```python
async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str = "http://localhost:8080",
    max_tool_iterations: int = 5,
    specialist_mode: str = "legacy",
    max_refinements: int = 3,
) -> dict:
    from agent.notifications import notify

    # Load per-turn state
    history = document_store.load_conversation_history(store, customer_id)
    context = await asyncio.to_thread(
        context_store.read_context, store, customer_id, customer_name
    )

    # Pre-populate _user_message so handlers can read it without changing
    # the ToolHandler signature. archie_wiring.py documents this pattern.
    # Each handler reads args.get("_user_message", "").
    #
    # We cannot pass user_message via the ToolHandler signature (it's frozen
    # by the protocol). Instead we inject it into context so ArchieMemory
    # exposes it in MemorySnapshot.raw, and handlers that need it call
    # args.get("_user_message") which archie_wiring would pre-inject.
    # Simplest bridge: store it in context for this turn only.
    context["_current_user_message"] = user_message

    forge = _get_forge(
        customer_id=customer_id,
        customer_name=customer_name,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
    )

    result = await forge.run_turn(
        session_id=customer_id,
        user_message=user_message,
        context=context,
        history=history,
    )

    # Save conversation turns
    new_turns = [
        {"role": "user", "content": user_message, "timestamp": _now(), "customer_name": customer_name},
        {"role": "assistant", "content": result.reply, "timestamp": _now()},
    ]
    document_store.save_conversation_turns(store, customer_id, new_turns)

    notify("turn:complete", customer_id, result.reply[:200])

    return {
        "reply": result.reply,
        "tool_calls": [
            {
                "tool": tc.tool,
                "args": tc.args,
                "result": tc.result.summary,
                "status": tc.result.status,
                "artifact_key": tc.result.artifact_key,
            }
            for tc in result.tool_calls
        ],
        "artifacts": result.artifacts,
        "history_length": result.history_length,
    }
```

**Important**: do NOT delete `_execute_tool`, `_execute_tool_core`, or any
helper function. They remain as dead code until a follow-on cleanup task
confirms the new path is stable in production.

## Test: `tests/test_archie_loop_cutover.py`

Use monkeypatch to avoid any OCI calls. The goal is to verify the new
`run_turn()` delegates to Forge and returns the expected dict shape.

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.mark.asyncio
async def test_run_turn_delegates_to_forge(monkeypatch, tmp_path):
    """run_turn() returns the expected dict shape from the Forge result."""
    from skillforge.types import TurnResult, ToolCall, ToolResult
    import agent.archie_loop as archie_loop

    fake_result = TurnResult(
        reply="Here is the architecture.",
        tool_calls=[],
        artifacts={},
        history_length=2,
    )

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.archie_loop.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="Generate a diagram",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert result["reply"] == "Here is the architecture."
    assert result["tool_calls"] == []
    assert result["artifacts"] == {}
    assert result["history_length"] == 2


@pytest.mark.asyncio
async def test_run_turn_includes_artifacts(monkeypatch):
    """Artifacts from Forge result appear in the returned dict."""
    from skillforge.types import TurnResult, ToolCall, ToolResult
    import agent.archie_loop as archie_loop

    tc = ToolCall(
        tool="generate_bom",
        args={},
        result=ToolResult(summary="BOM done", status="ok", artifact_key="bom/v1.xlsx"),
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

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.archie_loop.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="Generate BOM",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert result["artifacts"] == {"generate_bom": "bom/v1.xlsx"}
    assert result["tool_calls"][0]["tool"] == "generate_bom"
    assert result["tool_calls"][0]["artifact_key"] == "bom/v1.xlsx"
```

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0
2. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "_execute_tool\b" agent/archie_loop.py | grep -v "^def _execute_tool\|async def _execute_tool"` — returns nothing from `run_turn` (old call sites gone)
5. `grep "_forge_cache\|build_forge" agent/archie_loop.py` — matches (new delegation present)

## Do NOT Do

- Do not delete `_execute_tool` or `_execute_tool_core` — leave as dead code
- Do not change the `run_turn()` signature
- Do not add new logic to `run_turn()` — keep it as thin as possible
- Do not modify `archie_wiring.py` or any tool handler

## Rollback

If this task regresses `test_specialist_mode_routing.py`, revert `run_turn()` to
the old body. The old code is preserved in the file; the new delegation is
additive until tests confirm stability.

## Commit Message

```
p2i: cut archie_loop.run_turn() over to forge.run_turn() — Forge is now the orchestrator
```

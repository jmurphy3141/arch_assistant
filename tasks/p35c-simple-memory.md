# Task p35c: Add SimpleMemory — Zero-Config Memory for New Domain Teams

## Goal

Add `skillforge.SimpleMemory` — a concrete `Memory` implementation backed by
an in-memory dict. New teams adopting SkillForge can use it without writing
any Memory code. Teams graduate to a custom Memory only when they need real
persistence or OCI-specific context assembly.

---

## Prerequisite Check

```bash
python3.11 -c "from skillforge import Forge; print('ok')"
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass.

---

## Scope

**Only create:**
- `skillforge/memory.py`
- `tests/test_simple_memory.py`

**Only modify:**
- `skillforge/__init__.py` — add `SimpleMemory` to exports

**Do NOT touch `agent/`, `archie_wiring.py`, or `ArchieMemory`.**

---

## What to implement

### `skillforge/memory.py`

```python
"""
skillforge/memory.py
--------------------
SimpleMemory: zero-config in-memory Memory implementation.

Sufficient for new domain teams, testing, and the quickstart example.
Persists nothing across process restarts. Thread-safe within a single
asyncio event loop (no shared state between sessions).
"""
from __future__ import annotations

from typing import Any

from skillforge.types import MemorySnapshot, ToolResult


class SimpleMemory:
    """
    In-memory Memory implementation. No setup required.

    Stores facts, constraints, and artifacts extracted from ToolResult.data
    between turns. Each session_id has independent state.

    Usage:
        from skillforge import Forge, SimpleMemory
        forge = Forge(..., memory=SimpleMemory())
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot:
        state = self._store.get(session_id, {})
        return MemorySnapshot(
            session_id=session_id,
            facts=dict(state.get("facts") or {}),
            constraints=dict(state.get("constraints") or {}),
            artifacts=dict(state.get("artifacts") or {}),
            raw=state,
        )

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._store.setdefault(session_id, {})
        data = result.data or {}

        # Merge facts if provided
        if "facts" in data and isinstance(data["facts"], dict):
            state.setdefault("facts", {}).update(data["facts"])

        # Merge constraints if provided
        if "constraints" in data and isinstance(data["constraints"], dict):
            state.setdefault("constraints", {}).update(data["constraints"])

        # Track artifact keys
        if result.artifact_key:
            state.setdefault("artifacts", {})[tool_name] = result.artifact_key

        return context
```

### `skillforge/__init__.py` — add export

```python
from skillforge.memory import SimpleMemory
```

---

## Test: `tests/test_simple_memory.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/memory.py` exits 0
2. `pytest tests/test_simple_memory.py -v` — 7 passed
3. `from skillforge import SimpleMemory` works in a Python REPL
4. `pytest tests/test_forge.py -v` — no regressions
5. `grep "SimpleMemory" skillforge/__init__.py` — matches

---

## Do NOT Do

- Do not touch `ArchieMemory` or `archie_memory_impl.py`
- Do not add file I/O or OCI imports to `skillforge/memory.py`
- Do not make `SimpleMemory` thread-safe with locks — asyncio single-loop
  usage is the intended pattern

---

## Commit Message

```
p35c: add SimpleMemory — zero-config in-memory Memory for new domain teams
```

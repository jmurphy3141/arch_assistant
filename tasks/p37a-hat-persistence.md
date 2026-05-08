# Task p37a: Cross-Turn Hat Persistence

## Goal

`active_hats` resets to `[]` at the start of every `run_turn` call. An expert
hat activated in turn 1 is silently dropped in turn 2. The full infrastructure
for expert injection (`[ACTIVE EXPERT]` blocks, memory views, coordination rules)
already exists — it just needs hats to still be active across turns.

Fix: store `active_hats` in the `context` dict returned from `run_turn`, and
restore it at the start of the next turn.

---

## Scope

**Only modify:** `skillforge/forge.py`

**Do NOT touch:** `agent/archie_wiring.py`, hat files, memory modules, any handler.

---

## What to implement

### In `run_turn`

**Line ~263 (current):**
```python
active_hats: list[str] = []
```

**Replace with:**
```python
active_hats: list[str] = list(context.get("_active_hats", []))
```

**At the end of `run_turn`, before returning `TurnResult`**, update the context
with the current hat state:

```python
context["_active_hats"] = active_hats
```

This relies on the existing pattern where `run_turn` receives `context` as a
mutable dict and the caller re-passes it on the next turn. No new machinery needed.

### Also persist `hat_rounds`

`hat_rounds: dict[str, int]` tracks how many rounds each hat has been active
(used by `warn_stale_hats`). Restore and persist it the same way:

```python
# Restore at start of run_turn
hat_rounds: dict[str, int] = dict(context.get("_hat_rounds", {}))

# Persist at end of run_turn
context["_hat_rounds"] = hat_rounds
```

### Guard against stale hats

When restoring `active_hats` from context, filter out any hat names that are
no longer registered (hat file deleted):

```python
known = set(self._hat_engine.load_hats().keys())
active_hats = [h for h in context.get("_active_hats", []) if h in known]
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `grep "_active_hats" skillforge/forge.py` — at least 2 matches (restore + persist)
3. Unit test: run two sequential `run_turn` calls on the same `Forge` instance
   where the first turn activates a hat. Verify `active_hats` is non-empty at
   the start of the second turn's ReAct loop.
4. `pytest tests/test_forge.py -q --tb=short` — same pass count as before

---

## Commit Message

```
p37a: persist active_hats and hat_rounds across turns in context dict
```

Branch: `claude/p37a` (from main). Push when done.

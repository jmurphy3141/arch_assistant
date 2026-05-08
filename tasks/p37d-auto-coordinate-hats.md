# Task p37d: Auto-Execute Hat Coordination Rules

## Goal

p35m added coordination *suggestions* — status events that tell the LLM which
hats to consider. This task upgrades to *auto-execution*: when a hat's
`coordination.triggers` match the user message, Forge automatically activates
`recommended_hats` and any `parallel_with` hats without waiting for the LLM to
call `use_hat_*`.

p37a must be merged first (hat persistence).

---

## Scope

**Only modify:** `skillforge/forge.py`

**Do NOT touch:** `agent/hat_engine.py`, hat files, `archie_wiring.py`, handlers.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_active_hats" skillforge/forge.py   # p37a must be present
grep "get_coordination_rules" skillforge/forge.py  # p35m must be present
pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5
```

---

## What to implement

### 1. Add `auto_coordinate` flag to `Forge.__init__`

```python
def __init__(
    self,
    ...
    auto_coordinate: bool = True,   # NEW
):
    ...
    self._auto_coordinate = auto_coordinate
```

### 2. Replace suggestion-only logic with auto-execution

In `run_turn`, find the existing coordination trigger check (from p35m) that
currently only emits status events for `recommended_hats` and `parallel_with`.

Replace with:

```python
if self._auto_coordinate:
    for hat_name in list(active_hats):   # snapshot — avoid mutating while iterating
        coord = self._hat_engine.get_coordination_rules(hat_name)
        triggers = coord.get("triggers", [])
        msg_lower = user_message.lower()
        triggered = any(
            any(w.strip() in msg_lower for w in t.lower().split(","))
            for t in triggers
        )
        if not triggered:
            continue

        # Auto-activate recommended_hats
        for rec in coord.get("recommended_hats", []):
            if rec not in active_hats:
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, rec)
                    logger.info(
                        "Auto-activated hat '%s' via coordination rule of '%s' session=%s",
                        rec, hat_name, session_id,
                    )
                    yield TurnEvent(
                        type="status",
                        data={"message": f"Auto-activating expert '{rec}' for this request."}
                    )
                except ValueError:
                    pass  # hat not registered — skip silently

        # Auto-activate parallel_with hats
        for par in coord.get("parallel_with", []):
            if par not in active_hats:
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, par)
                    logger.info(
                        "Auto-activated parallel hat '%s' via coordination rule of '%s' session=%s",
                        par, hat_name, session_id,
                    )
                    yield TurnEvent(
                        type="status",
                        data={"message": f"Running '{par}' in parallel with '{hat_name}'."}
                    )
                except ValueError:
                    pass

# Also check transition suggestions for currently-inactive hats
suggestions = self._hat_engine.get_transition_suggestions(active_hats, user_message)
if suggestions and not self._auto_coordinate:
    # Only emit suggestion events when auto_coordinate is off
    yield TurnEvent(
        type="status",
        data={"message": f"Suggested hats for this request: {', '.join(suggestions)}"}
    )
```

Keep the existing `get_transition_suggestions` call for when `auto_coordinate=False`.
When `auto_coordinate=True`, suggestions are acted on directly rather than emitted.

### 3. Guard: do not auto-activate `critic` or `governor` via coordination

These hats have specific activation semantics. Skip them in auto-coordinate:

```python
_MANUAL_ONLY_HATS = {"critic", "governor"}

for rec in coord.get("recommended_hats", []):
    if rec in _MANUAL_ONLY_HATS:
        continue
    ...
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `grep "auto_coordinate" skillforge/forge.py` — at least 3 matches
   (`__init__`, flag check, log message)
3. `grep "_MANUAL_ONLY_HATS" skillforge/forge.py` — matches
4. Unit test: construct a `Forge` with `auto_coordinate=True`, a hat whose
   coordination triggers match the test message, and a `recommended_hat`.
   Run a turn — verify `recommended_hat` appears in `active_hats` before
   the first LLM call without the LLM calling `use_hat_*`.
5. `pytest tests/test_forge.py -q --tb=short` — same pass count as before

---

## Commit Message

```
p37d: auto-execute coordination rules — activate recommended and parallel hats without LLM call
```

Branch: `claude/p37d` (from main after p37a merges). Push when done.

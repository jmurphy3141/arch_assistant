# Task: Wire Hat Eviction into archie_loop.py

## Goal

Replace the manual hat list mutations in `agent/archie_loop.py` with calls to
`hat_engine.apply_hat` and `hat_engine.drop_hat`. Add round-counter tracking
so `hat_engine.warn_stale_hats` can be called each round.

## Prerequisite Check

Before making any changes, confirm PR #59 is merged and the new functions exist:

```bash
python3.11 -c "from agent.hat_engine import apply_hat, drop_hat, warn_stale_hats; print('ok')"
```

If this fails, stop and report. Do not proceed.

## Scope

**Only touch these files:**

- `agent/archie_loop.py` — replace hat mutations, add round counter
- `tests/test_specialist_mode_routing.py` — update any hat-related assertions if needed

**Do NOT touch:**

- `agent/hat_engine.py`
- `agent/archie_memory.py`
- `agent/safety_rules.py`
- Any other file

## What to Change

### 1. Add a rounds-active counter alongside `_active_hats`

Near line 222 where `_active_hats: list[str] = []` is declared, add:

```python
_hat_rounds: dict[str, int] = {}  # hat_name → rounds active
```

### 2. Replace the `use_hat_*` block (lines 931-935)

Current code:
```python
if hat_name in loaded_hats and hat_name not in _active_hats:
    _active_hats.append(hat_name)
```

Replace with:
```python
if hat_name in loaded_hats:
    _active_hats = hat_engine.apply_hat(_active_hats, hat_name)
```

Remove the `hat_name not in _active_hats` guard — `apply_hat` is idempotent.
The `ValueError` from `apply_hat` for unknown hats cannot fire here because we
already guard with `hat_name in loaded_hats`.

### 3. Replace the `drop_hat_*` block (lines 964-967)

Current code:
```python
if hat_name in _active_hats:
    _active_hats.remove(hat_name)
```

Replace with:
```python
_active_hats = hat_engine.drop_hat(_active_hats, hat_name)
_hat_rounds.pop(hat_name, None)
```

### 4. Increment round counters each ReAct iteration

Find the top of the ReAct loop (the `while` loop that calls the LLM). At the
start of each iteration, after checking `_active_hats` is not empty, add:

```python
for _h in _active_hats:
    _hat_rounds[_h] = _hat_rounds.get(_h, 0) + 1
stale = hat_engine.warn_stale_hats(_active_hats, _hat_rounds)
if stale:
    import logging as _logging
    _logging.getLogger(__name__).warning("Stale hats (active > 5 rounds): %s", stale)
```

The `import logging` must be at the top of the file (do not use inline import).
If `logging` is already imported, do not add it again.

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0.
2. `pytest tests/test_specialist_mode_routing.py -v` passes with zero failures.
3. `grep -n "_active_hats.append\|_active_hats.remove" agent/archie_loop.py`
   returns nothing — old direct mutations are gone.
4. `grep -n "apply_hat\|drop_hat" agent/archie_loop.py` returns at least 2 hits.

## Do NOT Do

- Do not change `hat_engine.py`
- Do not change `MAX_ACTIVE_HATS`
- Do not add any other logging beyond the stale-hat warning
- Do not touch any file not in the scope list
- Do not refactor the surrounding ReAct loop structure

## Commit Message

```
Wire hat eviction into archie_loop: use apply_hat/drop_hat, track stale hats
```

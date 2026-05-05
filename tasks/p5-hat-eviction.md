# Task: Hat Eviction Policy

## Goal

Add a maximum-active-hats limit with FIFO eviction to `agent/hat_engine.py`.
Right now hats accumulate indefinitely with no cap, which grows the prompt size
without bound and makes it impossible to reason about per-turn token cost.

## Scope

**Only touch these files:**

- `agent/hat_engine.py` — add eviction logic
- `tests/test_hat_engine.py` — add / update tests (create if it does not exist)

**Do NOT touch:**

- `agent/archie_loop.py`
- `agent/archie_memory.py`
- `agent/safety_rules.py`
- Any file not listed above

## Acceptance Criteria

1. `agent/hat_engine.py` exports a new constant `MAX_ACTIVE_HATS = 3`.
2. A new function `apply_hat(active_hats: list[str], hat_name: str) -> list[str]`
   implements the eviction contract:
   - If `hat_name` is already in `active_hats`, return `active_hats` unchanged
     (idempotent add — no duplicate, no eviction).
   - If `len(active_hats) < MAX_ACTIVE_HATS`, return `active_hats + [hat_name]`.
   - If `len(active_hats) >= MAX_ACTIVE_HATS`, evict the **oldest** hat (index 0)
     and return `active_hats[1:] + [hat_name]`.
   - If `hat_name` is not a known hat (not in `_HAT_CACHE`), raise `ValueError`.
3. A new function `drop_hat(active_hats: list[str], hat_name: str) -> list[str]`
   returns a copy of `active_hats` with `hat_name` removed (no-op if not present).
4. A new function `warn_stale_hats(active_hats: list[str], rounds_active: dict[str, int], max_rounds: int = 5) -> list[str]`
   returns the list of hat names that have been active for `> max_rounds` rounds
   without a drop call. Returns empty list if none are stale. No logging side effects.
5. `inject_hats` is unchanged in behaviour; it still takes `active_hats: list[str]`
   and returns the modified prompt.
6. All new functions have no logging side effects. The caller in `archie_loop.py`
   is responsible for any logging; keep `hat_engine.py` pure.
7. `pytest tests/test_hat_engine.py -v` passes with zero failures.
8. `python3.11 -m compileall agent/hat_engine.py` exits 0.

## Test Requirements

Write or update `tests/test_hat_engine.py` to cover:

- `apply_hat` idempotent add (duplicate hat → no change, no eviction)
- `apply_hat` normal add (under limit)
- `apply_hat` eviction (at limit → oldest evicted)
- `apply_hat` unknown hat raises `ValueError`
- `drop_hat` removes known hat
- `drop_hat` no-op for absent hat
- `warn_stale_hats` returns stale names correctly
- `warn_stale_hats` returns empty list when none are stale

## Prerequisite Check

Before making any changes, run:

```bash
python3.11 -m compileall agent/hat_engine.py
pytest tests/test_hat_engine.py -v 2>/dev/null || echo "no test file yet"
```

If compile fails, stop and report. Do not proceed.

## Do NOT Do

- Do not add any logging import or log calls to hat_engine.py
- Do not modify archie_loop.py to call the new functions — that is a separate task
- Do not change `MAX_ACTIVE_HATS` from 3
- Do not change `inject_hats` signature or behaviour
- Do not touch any file not in the scope list above
- Do not add docstrings beyond a single-line description per function

## Commit Message

```
Add hat eviction policy: MAX_ACTIVE_HATS=3, FIFO eviction, stale detection
```

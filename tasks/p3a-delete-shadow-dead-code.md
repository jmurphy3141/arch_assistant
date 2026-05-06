# Task p3a: Delete Shadow Dead Code from archie_loop.py

## Goal

Remove `_run_forge_shadow_turn()` and `_maybe_start_forge_shadow_turn()` from
`agent/archie_loop.py`. These functions were written for the p2i shadow-mode
experiment (fire-and-forget parallel Forge calls). After p2j, `run_turn()` IS
the Forge path — there is no legacy path left to shadow. Neither function is
called from anywhere in the live code.

Also delete the two unit tests that exercise these dead functions directly.

---

## Prerequisite Check

```bash
grep -n "_maybe_start_forge_shadow_turn" agent/archie_loop.py
```

Expected: exactly two lines — the `def` at ~264 and the `_run_forge_shadow_turn`
call at ~278. If you see a third line (a call site outside the function body),
stop and report — the function is still live.

```bash
pytest tests/test_archie_loop_shadow.py -v --tb=short 2>&1 | tail -4
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

Both must pass before you start.

---

## Scope

**Only modify:**

- `agent/archie_loop.py`
- `tests/test_archie_loop_shadow.py`

**Do NOT touch any other file.**

---

## Changes to `agent/archie_loop.py`

### Delete `_run_forge_shadow_turn` (~lines 219–261)

The function begins with:
```python
async def _run_forge_shadow_turn(
    *,
    customer_id: str,
```
and ends with:
```python
    except Exception:
        logger.exception("SkillForge shadow turn failed customer=%s", customer_id)
```

Delete the entire function including the trailing blank line.

### Delete `_maybe_start_forge_shadow_turn` (~lines 264–289)

The function begins with:
```python
def _maybe_start_forge_shadow_turn(
    *,
    customer_id: str,
```
and ends with:
```python
        name=f"skillforge-shadow:{customer_id}",
    )
```

Delete the entire function including the trailing blank line.

---

## Changes to `tests/test_archie_loop_shadow.py`

The file currently has two tests:

1. `test_shadow_disabled_does_not_schedule` — calls `_maybe_start_forge_shadow_turn` directly
2. `test_maybe_start_shadow_turn_does_fire_with_env` — calls `_maybe_start_forge_shadow_turn` directly

Both tests exercise a function that no longer exists. Delete the entire file
contents and replace with:

```python
"""Shadow dead-code tests removed in p3a — functions deleted from archie_loop."""
```

(A single-line module docstring so pytest collects zero tests without error.)

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0
2. `grep "_run_forge_shadow_turn\|_maybe_start_forge_shadow_turn" agent/archie_loop.py` — no output
3. `pytest tests/test_archie_loop_shadow.py -v` — 0 tests collected, no errors
4. `pytest tests/test_specialist_mode_routing.py -v` — no regressions (45 passed)
5. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed

---

## Do NOT Do

- Do not delete `_execute_tool`, `_execute_tool_core`, or any pre-routing helper
- Do not modify `run_turn()` — it is not affected by this deletion
- Do not touch `skillforge/` in this task

---

## Commit Message

```
p3a: delete shadow dead code (_run_forge_shadow_turn, _maybe_start_forge_shadow_turn)
```

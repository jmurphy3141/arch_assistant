# Task p3d: Delete _execute_tool and _execute_tool_core

## Goal

Remove `_execute_tool()` (~lines 1025–1389) and `_execute_tool_core()`
(~lines 1390–3200) from `agent/archie_loop.py`, along with all internal retry
loops that call `_execute_tool_core` directly.

After p3c, no production code calls either function. This deletion reduces
`archie_loop.py` by ~2,200 lines and removes the last remnant of the original
OCI-inline tool dispatch.

---

## Prerequisite Check

```bash
# Verify no live call sites remain
grep -n "await _execute_tool\b\|asyncio.gather.*_execute_tool" agent/archie_loop.py \
  | grep -v "^[0-9]*:async def\|^[0-9]*:    #"
```

Expected: **no output**. If you see any lines, p3c is incomplete — stop.

```bash
pytest tests/test_archie_loop_invoke_tool.py -v --tb=short 2>&1 | tail -3
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

Both must pass.

---

## Scope

**Only modify:**

- `agent/archie_loop.py`

**Do NOT create new test files** — existing tests are sufficient.
**Do NOT touch any other file.**

---

## What to delete

### 1. `_execute_tool` function body (~lines 1025–1389)

Find the function:
```python
async def _execute_tool(
    tool_name: str,
    args: dict,
    *,
    customer_id: str,
```

Delete from this line through the last line of its body (before
`async def _execute_tool_core`).

### 2. `_execute_tool_core` function body (~lines 1390–end of function)

Find:
```python
async def _execute_tool_core(
    tool_name: str,
    args: dict,
    *,
```

`_execute_tool_core` is a large function with multiple tool branches, retry
loops for BOM repair and diagram refinement (lines ~2561, ~2632, ~3134), and a
final fallback. Delete the entire function from its `async def` line through
the last line of its body.

### 3. Any helper functions that are only called from _execute_tool_core

After deleting both functions, run:
```bash
python3.11 -m compileall agent/archie_loop.py
```

If compile errors reference undefined names used only by the deleted functions
(e.g. `_compose_specialist_request_text`, `_build_tool_trace`), check whether
those helpers are called from anywhere else:
```bash
grep -n "_compose_specialist_request_text\|_build_tool_trace" agent/archie_loop.py
```

If a helper is called **only** from the deleted functions, delete it too.
If it is called from pre-routing or elsewhere, leave it.

---

## Cleanup: remove unused imports

After deletion, run:
```bash
python3.11 -m py_compile agent/archie_loop.py
```

Then check for any `F401` (unused import) warnings that were hidden by the
deleted code. Remove only imports that are now genuinely unused. Do not remove
any import that is still referenced in the file.

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py` exits 0
2. `grep "def _execute_tool\|def _execute_tool_core" agent/archie_loop.py` — no output
3. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed
4. `pytest tests/test_archie_loop_invoke_tool.py -v` — 2 passed
5. `pytest tests/test_specialist_mode_routing.py -v` — 45 passed (no regression)
6. `wc -l agent/archie_loop.py` — file is at least 2,000 lines shorter than before this task

---

## Do NOT Do

- Do not modify `skillforge/` or any tool handler
- Do not delete pre-routing helpers that are still called from `run_turn()`
- Do not delete `_get_forge`, `_forge_cache`, `_run_turn_with_forge`, or any
  function that is referenced in the live code path

---

## Commit Message

```
p3d: delete _execute_tool and _execute_tool_core — all dispatch through Forge
```

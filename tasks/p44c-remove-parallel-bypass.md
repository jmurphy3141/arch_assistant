# Task p44c: Remove parallel_tools Bypass Block

## Objective

The `parallel_tools` block in `archie_loop.py` (approx lines 816–907) detects
POV+JEP or BOM-only requests and dispatches them via `asyncio.gather()`,
returning before `forge.run_turn()` is reached.

Now that Forge handles orchestration and the system prompt carries parallelism
guidance, delete this block.

**IMPORTANT:** Branch from main AFTER p44b is merged.

---

## Scope

**Touch:**
- `agent/archie_loop.py` — delete parallel_tools block and helpers

**Do NOT touch:** `agent/archie_wiring.py`, `skillforge/`, hat files.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_loop.py
grep -n "parallel_tools\|parallel_executed\|asyncio.gather" \
  agent/archie_loop.py | wc -l
# note the count
```

---

## Changes

### `agent/archie_loop.py`

Locate the `parallel_tools` block (approx lines 816–907):
```python
    parallel_tools = _detect_parallel_tools(...)
    if parallel_tools:
        parallel_results = await asyncio.gather(...)
        ...
        parallel_executed = True
        ...

    if parallel_executed and not forced_reply:
        reply = _build_parallel_reply(...)
        return _finalize_turn(reply)
```

Delete the entire block including both `if parallel_tools:` and
`if parallel_executed and not forced_reply:` guards and their bodies.

Delete the `parallel_executed = False` initialisation near the top of
`run_turn()`.

Delete any now-unreferenced helpers:
- `_detect_parallel_tools()`
- `_build_parallel_reply()`
- Any other helpers only called from the deleted block

Use `grep -n "<function_name>" agent/archie_loop.py` before deleting each
helper to confirm no remaining callers.

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall agent/archie_loop.py
   ```

2. Parallel bypass gone:
   ```bash
   grep -n "parallel_tools\|parallel_executed\|asyncio.gather" \
     agent/archie_loop.py | wc -l
   # must be 0
   ```

3. forge.run_turn() still present:
   ```bash
   grep -n "forge.run_turn" agent/archie_loop.py | wc -l
   # must be >= 1
   ```

4. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p44c: remove parallel_tools bypass — POV/JEP/BOM parallel dispatch now handled by Forge
```

Branch: `claude/p44c` (from main, after p44b merged). Push when done.

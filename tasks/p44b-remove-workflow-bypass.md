# Task p44b: Remove workflow_plan and paired_bom_diagram_plan Bypass Blocks

## Objective

`archie_loop.py` intercepts generation requests via two large blocks
(`workflow_plan` lines 606–717 and `paired_bom_diagram_plan` lines 719–811)
before `forge.run_turn()` is ever reached. These blocks dispatch tools
directly, bypassing all of Forge's reasoning: step3_planning, requires_hat,
expert pre-action, expert post-review.

Now that p44a has added the sequencing rules to the Archie system prompt,
delete both blocks and let `forge.run_turn()` handle these requests.

**IMPORTANT:** Branch from main AFTER p44a is merged.

---

## Scope

**Touch:**
- `agent/archie_loop.py` — delete two bypass blocks and their helper functions

**Do NOT touch:** `agent/archie_wiring.py`, `skillforge/`, hat files.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_loop.py
grep -n "workflow_plan\|paired_bom_diagram_plan\|_generation_workflow_plan\|_bom_diagram_pair_plan" \
  agent/archie_loop.py | wc -l
# note the count — we will confirm it drops to zero after changes
```

---

## Changes

### `agent/archie_loop.py`

Read the file thoroughly before editing. Locate:

**Block 1 — workflow_plan** (approx lines 606–717):
```python
    workflow_plan = _generation_workflow_plan_for_message(...)
    if workflow_plan:
        ...
        return _finalize_turn(...)
```
Delete this entire block including the `if workflow_plan:` guard and its body.

**Block 2 — paired_bom_diagram_plan** (approx lines 719–811):
```python
    paired_bom_diagram_plan = _bom_diagram_pair_plan_for_message(...)
    if paired_bom_diagram_plan:
        ...
        return _finalize_turn(...)
```
Delete this entire block including the `if paired_bom_diagram_plan:` guard and
its body.

After deleting both blocks, also delete any module-level helper functions that
are now unreferenced:
- `_generation_workflow_plan_for_message()`
- `_bom_diagram_pair_plan_for_message()`
- `_build_generation_workflow_reply()`
- `_build_scenario_bom_prompt()`
- `_build_diagram_bom_text_from_bom_result()`
- `_build_downstream_workflow_prompt()`
- `_bom_result_can_feed_diagram()`
- Any other helpers only called from the deleted blocks

Use `grep -n "<function_name>" agent/archie_loop.py` to confirm each helper
has no remaining callers before deleting it.

Do NOT delete `_run_generation_step()` or `_invoke_prerouted_tool()` — they
may still be used by the remaining blocks (parallel_tools, update_confirm).

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall agent/archie_loop.py
   ```

2. Bypass blocks gone:
   ```bash
   grep -n "workflow_plan\|paired_bom_diagram_plan\|_generation_workflow_plan\|_bom_diagram_pair_plan" \
     agent/archie_loop.py | wc -l
   # must be 0
   ```

3. forge.run_turn() still present:
   ```bash
   grep -n "forge.run_turn\|forge_result" agent/archie_loop.py | wc -l
   # must be >= 3
   ```

4. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p44b: remove workflow_plan bypass — BOM/diagram/WAF requests now route through Forge
```

Branch: `claude/p44b` (from main, after p44a merged). Push when done.

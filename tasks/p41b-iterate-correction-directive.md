# Task p41b: Iterate Correction Directive

## Objective

When `_run_expert_post_review()` returns `"iterate"`, the concern is appended
to the prompt as `EXPERT_REVIEW (iterate): <concern>` and the loop calls
`continue`. The LLM sees the concern but receives no explicit directive. It may:

- Produce a plain reply explaining the concern (loop ends with error text)
- Call a different tool
- Re-call the right tool but with the same broken args

Fix: replace the bare `continue` with a block that appends an explicit
`CORRECTION REQUIRED` directive naming the tool and the concern. This gives the
LLM a clear, unambiguous instruction for the next iteration.

---

## Scope

**Touch:**
- `skillforge/forge.py` — update the `if review_decision == "iterate": continue`
  block in `run_turn()`

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "CORRECTION REQUIRED\|Re-call" skillforge/forge.py  # must be zero
```

---

## Change

Locate the iterate block in `run_turn()` (currently around the
`# Step 6` section):

```python
                if review_decision == "iterate":
                    # Expert found a fixable gap — continue loop for another attempt
                    # (do not fire critic; next iteration will re-plan and re-execute)
                    continue
```

Replace with:

```python
                if review_decision == "iterate":
                    # Extract the concern from the prompt (appended by post-review).
                    _iterate_concern = ""
                    if "EXPERT_REVIEW (iterate):" in prompt:
                        _iterate_concern = (
                            prompt.rsplit("EXPERT_REVIEW (iterate):", 1)[-1]
                            .splitlines()[0]
                            .strip()
                        )
                    _concern_clause = (
                        f" Specifically: {_iterate_concern}" if _iterate_concern else ""
                    )
                    prompt = (
                        f"{prompt}\n\n"
                        f"CORRECTION REQUIRED: The expert review found a fixable problem "
                        f"with the last '{tool_name}' call.{_concern_clause}\n"
                        f"Re-call '{tool_name}' now with corrected arguments that directly "
                        f"address this concern. Output ONLY the corrected tool call JSON."
                    )
                    continue
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Directive strings are present:
   ```bash
   grep "CORRECTION REQUIRED\|Re-call.*corrected arguments" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

3. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

4. No-hat path still works (same smoke test as p40a–d).

---

## Commit Message

```
p41b: iterate correction directive — explicit re-call instruction after expert review rejects
```

Branch: `claude/p41b` (from main, after p41a merged). Push when done.

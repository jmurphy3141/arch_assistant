# Task p40b: Iterate-Aware Pre-Action Context

## Objective

When the post-review returns `"iterate"` and the loop retries, the expert
pre-action call on the next iteration receives a prompt that includes
`EXPERT_REVIEW (iterate): <concern>` — but nothing tells the LLM explicitly
that it's on a retry or what iteration number it's on. The LLM may repeat the
same approach without understanding it's correcting a specific failure.

Add an iteration counter to `_run_expert_pre_action()` so it can prepend an
explicit RETRY CONTEXT block on iterations ≥ 1, surfacing the concern and the
attempt number.

---

## Scope

**Touch:**
- `skillforge/forge.py` — update `_run_expert_pre_action()` signature + call
  site in `run_turn()`

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "iteration.*pre_action\|RETRY CONTEXT\|attempt" skillforge/forge.py  # must be zero
```

---

## Changes

### 1. Update `_run_expert_pre_action()` signature

Add `iteration: int = 0` parameter:

```python
async def _run_expert_pre_action(
    self,
    *,
    prompt: str,
    tool_name: str,
    tool_args: dict,
    active_hats: list[str],
    session_id: str,
    events: list,
    iteration: int = 0,
) -> tuple[str, str | None]:
```

### 2. Add RETRY CONTEXT block to the pre-action prompt

After `hat_label = ", ".join(expert_hats)`, but **before** building
`pre_action_prompt`, add:

```python
    retry_context = ""
    if iteration > 0:
        # Extract the previous review concern from the prompt if present.
        concern = ""
        if "EXPERT_REVIEW (iterate):" in prompt:
            concern = prompt.rsplit("EXPERT_REVIEW (iterate):", 1)[-1].strip()
            # Trim to first line (the concern statement, not subsequent content).
            concern = concern.splitlines()[0].strip() if concern else ""
        retry_context = (
            f"\n\n⚠ RETRY CONTEXT — Attempt {iteration + 1}:\n"
            f"The previous attempt was rejected by the expert reviewer.\n"
            + (f"Reason: {concern}\n" if concern else "")
            + "Your pre-action reasoning and sub-agent instructions must directly "
            "address this failure.\n"
        )
```

Then prepend `retry_context` to `pre_action_prompt`:

```python
    pre_action_prompt = (
        f"{prompt}{retry_context}\n\n"
        "╔══════════════════════════════════╗\n"
        ...
    )
```

### 3. Update the call site in `run_turn()`

Pass `iteration=iteration` at the call site:

```python
            expert_hats_active = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
            if expert_hats_active:
                prompt, clarification_needed = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                    events=events,
                    iteration=iteration,
                )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Iteration parameter is present in signature and call site:
   ```bash
   grep "iteration=iteration\|iteration: int" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

3. RETRY CONTEXT block is present:
   ```bash
   grep "RETRY CONTEXT\|Attempt.*iteration\|previous attempt was rejected" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

4. No-hat path still works (same smoke test as p40a).

5. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p40b: iteration-aware pre-action — surface retry context and attempt number
```

Branch: `claude/p40b` (from main, after p40a merged). Push when done.

# Task p39c: Strengthen Step 6 — Expert Post-Review with Iterate/Surface Decision

## Objective

Add `_run_expert_post_review()` to `skillforge/forge.py`. After a
critique-enabled tool returns ok, the manager (still wearing its expert hat)
reviews the result, decides whether to approve, iterate, or surface to the
user — and logs its reasoning. Only after approval does the critic hat fire.

This is Step 6 of the manager reasoning loop. It completes the loop that
p39a began.

---

## Scope

**Only touch:** `skillforge/forge.py`.  
**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_run_expert_post_review" skillforge/forge.py  # must be zero before edit
grep "_run_expert_pre_action" skillforge/forge.py   # must be ≥ 2 (from p39a)
```

---

## What to implement

### 1. Add `_run_expert_post_review()` private method

Add this method to the `Forge` class, adjacent to `_run_expert_pre_action()`
and `_run_critique_pass()`:

```python
_EXPERT_REVIEW_APPROVED = "EXPERT_APPROVED"
_EXPERT_REVIEW_ITERATE  = "EXPERT_ITERATE:"
_EXPERT_REVIEW_SURFACE  = "EXPERT_SURFACE:"

async def _run_expert_post_review(
    self,
    *,
    prompt: str,
    tool_name: str,
    result: ToolResult,
    active_hats: list[str],
    session_id: str,
) -> tuple[str, str]:
    """
    Step 6 of the manager reasoning loop: expert post-action review.

    The manager, still wearing the active expert hat, reviews the sub-agent
    result against the hat's Post-Action Review checklist.

    Returns:
        (updated_prompt, decision)
        decision is one of:
          "approved"  — all checks pass; critic may fire
          "iterate"   — fixable gap found; caller should retry the tool
          "surface"   — unfixable gap; caller should return to user

    Logs expert review at INFO level. No-op (returns "approved") when no
    expert hat is active.
    """
    expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
    if not expert_hats:
        return prompt, "approved"

    hat_label = ", ".join(expert_hats)
    review_prompt = (
        f"{prompt}\n\n[STEP 6 — EXPERT POST-ACTION REVIEW]\n"
        f"You are wearing the {hat_label} hat. Review the result of '{tool_name}' "
        "above against your hat's ## Post-Action Review checklist.\n\n"
        "Respond with EXACTLY ONE of:\n"
        f"  {_EXPERT_REVIEW_APPROVED}   — all checks pass\n"
        f"  {_EXPERT_REVIEW_ITERATE} <specific issue>   — fixable; describe what to correct\n"
        f"  {_EXPERT_REVIEW_SURFACE} <explanation>   — unfixable; explain what to tell the user\n\n"
        "Do NOT call a tool here. Be specific about any failing check."
    )
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(review_prompt, system_msg, "expert_post_review")
    except Exception:
        logger.exception(
            "Expert post-review call failed session=%s tool=%s", session_id, tool_name
        )
        return prompt, "approved"

    review_text = raw.strip()
    logger.info(
        "Expert post-review [%s] for tool '%s' session=%s:\n%s",
        hat_label, tool_name, session_id, review_text,
    )

    if review_text.startswith(_EXPERT_REVIEW_ITERATE):
        concern = review_text[len(_EXPERT_REVIEW_ITERATE):].strip()
        prompt = f"{prompt}\n\nEXPERT_REVIEW (iterate): {concern}"
        return prompt, "iterate"

    if review_text.startswith(_EXPERT_REVIEW_SURFACE):
        concern = review_text[len(_EXPERT_REVIEW_SURFACE):].strip()
        prompt = f"{prompt}\n\nEXPERT_REVIEW (surface): {concern}"
        return prompt, "surface"

    # EXPERT_APPROVED or anything else → treat as approved
    return prompt, "approved"
```

### 2. Wire `_run_expert_post_review()` into `run_turn()`

In `run_turn()`, locate the post-tool critic block added in p39a:

```python
            # Post-tool expert self-review (Step 6) then critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt = await self._run_expert_review(...)   # old p39a name if present
                prompt, active_hats = await self._run_critique_pass(...)
```

Or the original (pre-p39a) block:

```python
            # Post-tool critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt, active_hats = await self._run_critique_pass(...)
```

Replace the entire `if spec.critique_enabled and result.status == "ok":` block
with the following. This handles all three decisions from the expert review:

```python
            # Step 6: expert post-review, then critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt, review_decision = await self._run_expert_post_review(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )
                if review_decision == "surface":
                    # Expert found an unfixable gap — surface to user
                    surface_msg = prompt.rsplit("EXPERT_REVIEW (surface):", 1)[-1].strip()
                    reply = surface_msg
                    break
                if review_decision == "iterate":
                    # Expert found a fixable gap — continue loop for another attempt
                    # (do not fire critic; next iteration will re-plan and re-execute)
                    continue
                # "approved" — fire the critic
                prompt, active_hats = await self._run_critique_pass(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. `_run_expert_post_review` present at definition + call site:
   ```bash
   grep "_run_expert_post_review" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

3. Expert review fires BEFORE critic pass in source order:
   ```bash
   python3.11 -c "
   import inspect, skillforge.forge as f
   src = inspect.getsource(f.Forge.run_turn)
   post_pos = src.index('_run_expert_post_review')
   critic_pos = src.index('_run_critique_pass')
   assert post_pos < critic_pos, 'post_review must appear before critique_pass'
   print('ordering OK')
   "
   ```

4. Three decision strings are defined as module constants:
   ```bash
   grep "EXPERT_REVIEW_APPROVED\|EXPERT_REVIEW_ITERATE\|EXPERT_REVIEW_SURFACE" skillforge/forge.py | wc -l
   # must be ≥ 3
   ```

5. Expert review logs at INFO level:
   ```bash
   grep "logger.info.*expert_post_review\|logger.info.*Expert post-review" skillforge/forge.py
   ```

6. No-hat path still works (no regression):
   ```bash
   python3.11 -c "
   import asyncio
   from skillforge.forge import Forge
   from skillforge.types import MemorySnapshot

   class NullMemory:
       def assemble(self, *, session_id, context, user_message):
           return MemorySnapshot(raw={}, formatted='')
       def update(self, *, session_id, tool_name, result, context):
           return context

   class NullHatEngine:
       def load_hats(self): return {}
       def apply_hat(self, hats, name): return hats
       def drop_hat(self, hats, name): return hats
       def warn_stale_hats(self, hats, rounds): return []
       def inject_hats(self, prompt, hats): return prompt
       def get_hat_tool_definitions(self): return []
       def build_expert_block(self, name): return ''
       def build_memory_view_block(self, name, snap): return ''
       def get_transition_suggestions(self, hats, msg): return []
       def get_suggested_next_hat(self, name): return None
       def get_coordination_rules(self, name): return {}
       def get_hat_meta(self, name): return {}
       def get_parallel_hats(self, name): return []
       def get_handoff_message(self, name): return None

   async def null_runner(prompt, system_msg, role):
       return 'plain reply'

   forge = Forge(
       base_system_prompt='You are an assistant.',
       hat_engine=NullHatEngine(),
       memory=NullMemory(),
       text_runner=null_runner,
   )
   result = asyncio.run(forge.run_turn(
       session_id='test', user_message='hello', context={}
   ))
   assert result.reply == 'plain reply'
   print('no-hat path OK')
   "
   ```

7. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p39c: expert post-review (Step 6) with iterate/surface/approve decision before critic
```

Branch: `claude/p39c` (from `claude/p39b`). Push when done.

# Task p39c: Forge Structured Turn — Planning Call + Expert Self-Review

## Goal

Extend `skillforge/forge.py` with two new private methods:

1. `_run_planning_call()` — fires **before** the first ReAct iteration when
   hats are active. Produces a structured Step 1–3 plan (plain text) and
   appends it to the running prompt so all subsequent iterations see it.

2. `_run_expert_review()` — fires **after** a critique_enabled tool returns ok
   and **before** `_run_critique_pass()`. Reviews the result while the expert
   hat is still active. Appends any expert concerns as `EXPERT_REVIEW:` notes.

No changes to `__init__`, `register_tool`, or the external API.  
No new public methods. Only `run_turn()` and the two new private helpers change.

---

## Scope

**Only touch:** `skillforge/forge.py`.  
**Do NOT touch:** tests, hat files, skill files, or other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "planning_call\|expert_review" skillforge/forge.py  # should be zero before edit
```

---

## What to implement

### 1. Add `_run_planning_call()` private method

Add this method to the `Forge` class, near `_run_critique_pass()` (around
line 725 in the original file):

```python
async def _run_planning_call(
    self,
    *,
    prompt: str,
    active_hats: list[str],
    session_id: str,
) -> str:
    """
    Fire a lightweight pre-loop planning call (Steps 1–3 of the reasoning loop).

    Returns the updated prompt with the planning output appended.
    If the call fails, returns the original prompt unchanged.
    Only fires when at least one non-critic, non-governor hat is active.
    """
    expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
    if not expert_hats:
        return prompt

    planning_prompt = (
        f"{prompt}\n\n[PLANNING — Steps 1–3]\n"
        "Before calling any tool, reason through Steps 1–3 of the manager "
        "reasoning loop:\n"
        "Step 1: What is the user's real goal?\n"
        "Step 2: What is already known? What is missing?\n"
        "Step 3: What is the plan? Which tool (if any) will be called next?\n"
        "Output your reasoning as plain text. Do NOT call a tool here."
    )
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(planning_prompt, system_msg, "planning")
    except Exception:
        logger.exception(
            "Planning call failed session=%s", session_id
        )
        return prompt

    plan_text = raw.strip()
    if plan_text:
        prompt = f"{prompt}\n\nPLANNING:\n{plan_text}"
    return prompt
```

### 2. Add `_run_expert_review()` private method

Add this method adjacent to `_run_planning_call()`:

```python
async def _run_expert_review(
    self,
    *,
    prompt: str,
    tool_name: str,
    result: ToolResult,
    active_hats: list[str],
    session_id: str,
) -> str:
    """
    Fire an expert self-review (Step 6) after a critique_enabled tool returns ok.

    The active expert hat is still present; it reviews the result against its
    own Post-Action Review checklist before the critic hat fires.
    Returns the updated prompt with any expert concerns appended.
    If the call fails or no expert hat is active, returns the original prompt.
    """
    expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
    if not expert_hats:
        return prompt

    review_prompt = (
        f"{prompt}\n\n[EXPERT SELF-REVIEW — Step 6]\n"
        f"You just executed '{tool_name}'. Review the result above using "
        "your hat's '## Post-Action Review' checklist.\n"
        "If all checks pass, output: EXPERT_APPROVED\n"
        "If any check fails, describe the specific issue(s) as plain text. "
        "Do NOT call a tool here."
    )
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(review_prompt, system_msg, "expert_review")
    except Exception:
        logger.exception(
            "Expert review call failed session=%s tool=%s", session_id, tool_name
        )
        return prompt

    review_text = raw.strip()
    if review_text and review_text != "EXPERT_APPROVED":
        prompt = f"{prompt}\n\nEXPERT_REVIEW: {review_text}"
    return prompt
```

### 3. Wire `_run_planning_call()` into `run_turn()`

In `run_turn()`, add the planning call **after** the initial memory assembly
and hat coordination block, **before** the `for iteration in range(...)` loop.

The insertion point is just before `for iteration in range(self._max_iterations):`.

Add these lines (preserve all surrounding indentation):

```python
        # Step 1–3 planning call (fires once per turn when expert hats are active)
        prompt = await self._run_planning_call(
            prompt=prompt,
            active_hats=active_hats,
            session_id=session_id,
        )

        for iteration in range(self._max_iterations):
```

Replace the existing bare `for iteration in range(self._max_iterations):` line
with the block above. The `for` line itself does not change — you only prepend
the planning call block before it.

### 4. Wire `_run_expert_review()` into `run_turn()`

In `run_turn()`, locate the existing post-tool critic block (approximately):

```python
            # Post-tool critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt, active_hats = await self._run_critique_pass(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )
```

Replace it with the expert review + critic sequence:

```python
            # Post-tool expert self-review (Step 6) then critic pass
            if spec.critique_enabled and result.status == "ok":
                prompt = await self._run_expert_review(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                )
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

2. Planning call is present and wired:
   ```bash
   grep "_run_planning_call\|planning_call\|Step 1.*Step 2\|PLANNING:" skillforge/forge.py
   ```
   Must match at least 3 lines.

3. Expert review is present and wired:
   ```bash
   grep "_run_expert_review\|post_action\|EXPERT_REVIEW\|Post-Action" skillforge/forge.py
   ```
   Must match at least 3 lines.

4. Expert review fires BEFORE critic pass:
   ```bash
   python3.11 -c "
   import inspect, skillforge.forge as f
   src = inspect.getsource(f.Forge.run_turn)
   expert_pos = src.index('_run_expert_review')
   critic_pos = src.index('_run_critique_pass')
   assert expert_pos < critic_pos, 'Expert review must fire before critic'
   print('ordering OK')
   "
   ```

5. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```
   Pass count must be identical to pre-change baseline.

6. Forge still instantiates and processes a turn without error when no hats
   are active (planning call and expert review are no-ops):
   ```bash
   python3.11 -c "
   import asyncio
   from skillforge.forge import Forge
   from skillforge.protocols import Memory, HatEngine
   from skillforge.types import MemorySnapshot, ToolResult

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
   assert result.reply == 'plain reply', f'Got: {result.reply}'
   print('no-hat run_turn OK')
   "
   ```

---

## Commit Message

```
p39c: Forge planning call (Steps 1–3) + expert self-review (Step 6) before critic
```

Branch: `claude/p39c` (from `claude/p39a` merge into `claude/p39b`, then merge
both into `claude/p39c`).

**Or** simply branch from main and apply all three diffs (p39a, p39b, p39c)
onto a single branch, since the files don't conflict.

Push when done.

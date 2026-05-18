# Task p39e: Expert Depth, Quality Bar Check, Memory Consistency, Shallow-Response Guard

## Objective

Three remaining gaps from the p39d assessment:

1. **Pre-hat depth enforcement** — the 4-section prompt exists but nothing stops the LLM
   from writing one-word answers per section. Add a minimum-depth guard with retry.

2. **Post-hat Quality Bar + memory consistency** — the review checks the hat's
   `## Post-Action Review` checklist but does NOT explicitly cross-check the result
   against (a) the hat's `## Quality Bar` section or (b) the in-scope memory snapshot
   values. Both are now required.

3. **Forge shallow-response guard** — if either expert LLM call returns fewer than
   `_EXPERT_THINKING_MIN_CHARS` characters, Forge retries once with a stronger prompt
   before logging a shallow-response warning.

---

## Scope

**Touch:**
- `skillforge/forge.py` — update `_run_expert_pre_action()` and `_run_expert_post_review()`
- `tasks/p39a-manager-reasoning-loop-skill.md` — update pre-action method spec
- `tasks/p39c-forge-structured-loop.md` — update post-review method spec

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_EXPERT_THINKING_MIN_CHARS" skillforge/forge.py  # must be zero before edit
grep "Quality Bar\|memory_snapshot" skillforge/forge.py  # baseline
```

---

## Fix 1 — Pre-Hat Depth Guard (minimum characters + single retry)

### What to add to `_run_expert_pre_action()`

After the primary LLM call and before logging, check response depth. If the
reasoning is too short, retry once with a stronger prompt:

```python
_EXPERT_THINKING_MIN_CHARS = 300   # module-level constant
```

Add after `reasoning = raw.strip()`:

```python
    # Shallow-response guard: retry once if response is too brief
    if len(reasoning) < _EXPERT_THINKING_MIN_CHARS and not reasoning.startswith("NEEDS_CLARIFICATION:"):
        logger.warning(
            "[EXPERT_PRE_ACTION] Shallow response (%d chars) for tool '%s' session=%s — retrying",
            len(reasoning), tool_name, session_id,
        )
        retry_prompt = (
            f"{pre_action_prompt}\n\n"
            "[Your previous response was too brief. A senior expert would write at least "
            "3 specific bullet points per section. Retry with full depth — be specific "
            "about values, part numbers, topologies, or module names as appropriate.]"
        )
        try:
            raw = await self._text_runner(retry_prompt, system_msg, "expert_pre_action_retry")
            reasoning = raw.strip()
        except Exception:
            logger.exception(
                "[EXPERT_PRE_ACTION] Retry failed session=%s tool=%s", session_id, tool_name
            )
        if len(reasoning) < _EXPERT_THINKING_MIN_CHARS:
            logger.warning(
                "[EXPERT_PRE_ACTION] Still shallow after retry (%d chars) session=%s tool=%s",
                len(reasoning), tool_name, session_id,
            )
```

---

## Fix 2 — Post-Review: Quality Bar Check + Memory Consistency

### What to change in `_run_expert_post_review()`

The method currently receives `result: ToolResult` and `active_hats`. It needs
to also accept `memory_snapshot: MemorySnapshot | None` so it can include
in-scope memory values in the review prompt.

Update the method signature:

```python
async def _run_expert_post_review(
    self,
    *,
    prompt: str,
    tool_name: str,
    result: ToolResult,
    active_hats: list[str],
    session_id: str,
    events: list,
    memory_snapshot: "MemorySnapshot | None" = None,
) -> tuple[str, str]:
```

Update the review prompt to add two explicit phases before the final decision:

```python
    # Build memory context string for the review
    memory_context = ""
    if memory_snapshot is not None:
        formatted = getattr(memory_snapshot, "formatted", "") or ""
        if formatted.strip():
            memory_context = (
                "\n\nMEMORY SNAPSHOT (confirmed values from this session):\n"
                f"{formatted.strip()}\n"
            )

    review_prompt = (
        f"{prompt}{memory_context}\n\n"
        "╔══════════════════════════════════╗\n"
        "║  STEP 6 — EXPERT POST-REVIEW     ║\n"
        "╚══════════════════════════════════╝\n"
        f"You are wearing the [{hat_label}] hat. You received the result of "
        f"'{tool_name}'. You are NOT rubber-stamping — review honestly.\n\n"
        "PHASE A — Quality Bar check:\n"
        "Work through each item in your hat's ## Quality Bar section.\n"
        "For each item write: PASS or FAIL: <specific value that was wrong>\n\n"
        "PHASE B — Post-Action Review checklist:\n"
        "Work through each item in your hat's ## Post-Action Review section.\n"
        "For each item write: PASS or FAIL: <specific field and expected value>\n\n"
        "PHASE C — Memory consistency check:\n"
        "Compare the result against the MEMORY SNAPSHOT above.\n"
        "Flag any value in the result that contradicts confirmed memory "
        "(e.g. wrong region, wrong shape, wrong HA mode).\n"
        "Write: CONSISTENT or CONFLICT: <field> expected=<memory value> got=<result value>\n\n"
        "FINAL DECISION — after completing Phases A, B, and C, output EXACTLY ONE line:\n"
        f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
        f"  {_EXPERT_REVIEW_ITERATE} <issue>    — at least one fixable FAIL or CONFLICT\n"
        f"  {_EXPERT_REVIEW_SURFACE} <issue>    — unfixable gap requiring user clarification\n\n"
        "You MUST complete all three phases before writing the final decision line.\n"
        "Do NOT call a tool here."
    )
```

Update the call site in `run_turn()` to pass the current `memory_snapshot`:

```python
                prompt, review_decision = await self._run_expert_post_review(
                    prompt=prompt,
                    tool_name=tool_name,
                    result=result,
                    active_hats=active_hats,
                    session_id=session_id,
                    events=events,
                    memory_snapshot=memory_snapshot,
                )
```

---

## Fix 3 — Shallow-Response Guard for Post-Review

Apply the same minimum-depth guard to `_run_expert_post_review()`. The three-phase
review should be significantly longer than `_EXPERT_THINKING_MIN_CHARS`. Use a
higher threshold:

```python
_EXPERT_REVIEW_MIN_CHARS = 500   # module-level constant (post-review is longer)
```

After `review_text = raw.strip()`, add:

```python
    if len(review_text) < _EXPERT_REVIEW_MIN_CHARS:
        logger.warning(
            "[EXPERT_POST_REVIEW] Shallow response (%d chars) for tool '%s' session=%s — retrying",
            len(review_text), tool_name, session_id,
        )
        retry_prompt = (
            f"{review_prompt}\n\n"
            "[Your response was too brief. You must complete all three phases "
            "(Quality Bar, Post-Action Review, Memory Consistency) with a PASS/FAIL "
            "or CONSISTENT/CONFLICT for every item before writing the final decision.]"
        )
        try:
            raw = await self._text_runner(retry_prompt, system_msg, "expert_post_review_retry")
            review_text = raw.strip()
        except Exception:
            logger.exception(
                "[EXPERT_POST_REVIEW] Retry failed session=%s tool=%s", session_id, tool_name
            )
        if len(review_text) < _EXPERT_REVIEW_MIN_CHARS:
            logger.warning(
                "[EXPERT_POST_REVIEW] Still shallow after retry (%d chars) session=%s",
                len(review_text), session_id,
            )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Module-level depth constants are defined:
   ```bash
   grep "_EXPERT_THINKING_MIN_CHARS\|_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

3. Shallow-response retry is present in both methods:
   ```bash
   grep "Shallow response\|expert_pre_action_retry\|expert_post_review_retry" skillforge/forge.py | wc -l
   # must be ≥ 4
   ```

4. Quality Bar and memory consistency are explicitly mentioned in the review prompt:
   ```bash
   grep "Quality Bar\|Memory consistency\|MEMORY SNAPSHOT\|CONFLICT:" skillforge/forge.py
   # must match
   ```

5. Three-phase review structure is present:
   ```bash
   grep "PHASE A\|PHASE B\|PHASE C" skillforge/forge.py | wc -l
   # must be ≥ 3
   ```

6. `memory_snapshot` is passed to `_run_expert_post_review()`:
   ```bash
   grep "memory_snapshot=memory_snapshot" skillforge/forge.py
   # must match
   ```

7. No-hat path still works (neither guard fires when no hats active):
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

8. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p39e: depth guard + Quality Bar/memory consistency review + shallow-response retry
```

Branch: `claude/p39e` (from main, after p39a–p39d merged). Push when done.

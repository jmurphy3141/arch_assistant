# Task p39d: Reasoning Loop Hardening — Depth, Consistency, Visibility, Review Strength

## Objective

Four targeted fixes to the p39a/p39c implementation:

1. **Depth** — Force structured expert reasoning (not freeform bullets)
2. **Consistency** — Fire pre-action for ALL domain tool calls when a hat is active,
   not only `critique_enabled` tools
3. **Visibility** — Emit `TurnEvent` for expert thinking steps so callers observe them
4. **Post-Review Strength** — Require explicit per-item checklist confirmation before
   `EXPERT_APPROVED` is valid

---

## Scope

**Touch:**
- `skillforge/forge.py` — update `_run_expert_pre_action()`, `_run_expert_post_review()`,
  and their call sites in `run_turn()`
- `tasks/p39a-manager-reasoning-loop-skill.md` — update the method spec to match
- `tasks/p39c-forge-structured-loop.md` — update the method spec to match

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_run_expert_pre_action\|_run_expert_post_review" skillforge/forge.py  # must be ≥ 4 lines total (from p39a/p39c)
```

---

## Fix 1 — Depth: Structured Pre-Action Prompt

### Problem
Current prompt asks the LLM to "cover: 1. Known facts 2. Gaps 3. Approach 4. Instructions"
but imposes no structure. The LLM can produce shallow text and move on.

### Fix
Replace the freeform prompt in `_run_expert_pre_action()` with a structured format
that requires four labeled sections. Update the method body as follows:

```python
    hat_label = ", ".join(expert_hats)
    pre_action_prompt = (
        f"{prompt}\n\n"
        "╔══════════════════════════════════╗\n"
        "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
        "╚══════════════════════════════════╝\n"
        f"You are wearing the [{hat_label}] hat. You ARE the expert.\n"
        f"Before calling '{tool_name}', produce your expert reasoning using "
        "EXACTLY this structure:\n\n"
        "KNOWN FACTS:\n"
        "- [List every confirmed value: shape, region, OCPU, memory, storage, HA mode, "
        "budget, compliance scope, etc. Be specific — no vague summaries.]\n\n"
        "GAPS:\n"
        "- [List every unconfirmed prerequisite from this hat's Pre-Action Checklist. "
        "If none, write 'None — all prerequisites confirmed.']\n\n"
        "EXPERT ASSESSMENT:\n"
        "- [As the expert, what is the right solution? State your recommendation "
        "with specifics (shape names, SKUs, topology, module names) — not generic advice.]\n\n"
        "SUB-AGENT INSTRUCTIONS:\n"
        "- [Exact task description you will pass to the sub-agent. Be precise.]\n\n"
        "Do NOT call a tool here. If GAPS is non-empty and contains starred (★) "
        "required items, output only: NEEDS_CLARIFICATION: <question>"
    )
```

Also update `_run_expert_pre_action()` to handle `NEEDS_CLARIFICATION:` returns:
after `reasoning = raw.strip()`, add:

```python
    if reasoning.startswith("NEEDS_CLARIFICATION:"):
        clarification = reasoning[len("NEEDS_CLARIFICATION:"):].strip()
        logger.info(
            "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s → NEEDS_CLARIFICATION: %s",
            hat_label, tool_name, session_id, clarification,
        )
        # Signal caller to surface the clarification instead of calling the tool
        return prompt, clarification   # second return value added — see Fix 2
```

This requires the method signature to return `tuple[str, str | None]` instead of `str`:

```python
async def _run_expert_pre_action(
    self,
    *,
    prompt: str,
    tool_name: str,
    tool_args: dict,
    active_hats: list[str],
    session_id: str,
) -> tuple[str, str | None]:
    """
    Returns (updated_prompt, clarification_needed).
    clarification_needed is None when the expert is ready to proceed.
    clarification_needed is a question string when a starred prerequisite is unmet.
    """
    ...
    return prompt, None   # normal case at end of method
```

---

## Fix 2 — Consistency: Fire for All Domain Tool Calls When Hat is Active

### Problem
Current call site in `run_turn()`:
```python
            if spec.critique_enabled:
                prompt = await self._run_expert_pre_action(...)
```
Tools not marked `critique_enabled` skip expert thinking entirely even when a hat is active.

### Fix
Change the call site to fire whenever **any expert hat is active**, regardless of
`critique_enabled`. The `critique_enabled` flag now only controls the post-review
and critic pass — not the pre-action thinking.

Replace the current pre-action call site with:

```python
            # Step 4: expert pre-action thinking (fires for any domain tool when hat active)
            expert_hats_active = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
            if expert_hats_active:
                prompt, clarification_needed = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                )
                if clarification_needed:
                    reply = clarification_needed
                    break
```

---

## Fix 3 — Visibility: Emit TurnEvents for Expert Thinking Steps

### Problem
Expert thinking only appears in server logs (`logger.info`). Callers of `run_turn()`
receive `TurnResult` with `events: list[TurnEvent]` but expert thinking steps are
invisible to them.

### Fix
In `_run_expert_pre_action()`, after building `reasoning`, append a `TurnEvent`
to the events list. Since the method doesn't have direct access to `events`, pass
it as a parameter:

Update signature to accept `events`:
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
) -> tuple[str, str | None]:
```

After logging, emit the event:
```python
    if reasoning and not reasoning.startswith("NEEDS_CLARIFICATION:"):
        logger.info(
            "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s:\n%s",
            hat_label, tool_name, session_id, reasoning,
        )
        events.append(
            TurnEvent(
                type="expert_pre_action",
                message=f"Expert pre-action [{hat_label}] for '{tool_name}'",
                data={"hat": hat_label, "tool": tool_name, "reasoning": reasoning},
            )
        )
        prompt = f"{prompt}\n\nEXPERT_THINKING:\n{reasoning}"
```

Apply the same pattern to `_run_expert_post_review()`:
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
) -> tuple[str, str]:
```

After logging:
```python
    logger.info(
        "[EXPERT_POST_REVIEW] [%s] tool='%s' session=%s decision=%s:\n%s",
        hat_label, tool_name, session_id, decision, review_text,
    )
    events.append(
        TurnEvent(
            type="expert_post_review",
            message=f"Expert post-review [{hat_label}] for '{tool_name}': {decision}",
            data={"hat": hat_label, "tool": tool_name, "decision": decision, "review": review_text},
        )
    )
```

Update all call sites to pass `events=events`.

---

## Fix 4 — Post-Review Strength: Require Explicit Per-Item Confirmation

### Problem
Current review prompt allows the LLM to output `EXPERT_APPROVED` after reading
one word. There's no forcing function to actually check each item.

### Fix
Replace the review prompt in `_run_expert_post_review()` with a two-phase structure
that requires checking before approving:

```python
    review_prompt = (
        f"{prompt}\n\n"
        "╔══════════════════════════════════╗\n"
        "║  STEP 6 — EXPERT POST-REVIEW     ║\n"
        "╚══════════════════════════════════╝\n"
        f"You are wearing the [{hat_label}] hat. You just received the result of "
        f"'{tool_name}'. Review it honestly — you are not rubber-stamping.\n\n"
        "Work through EACH item in your hat's ## Post-Action Review checklist.\n"
        "For each item write: PASS or FAIL: <what is wrong and what value was expected>\n\n"
        "After checking all items, output EXACTLY ONE final line:\n"
        f"  {_EXPERT_REVIEW_APPROVED}          — only if every item is PASS\n"
        f"  {_EXPERT_REVIEW_ITERATE} <correction> — if a fixable item failed\n"
        f"  {_EXPERT_REVIEW_SURFACE} <explanation> — if an unfixable item failed\n\n"
        "You MUST check every checklist item before writing the final line.\n"
        "Do NOT call a tool here."
    )
```

Update the decision-parsing logic to find the final line rather than the first line:

```python
    # Find the decision on the LAST non-empty line (after per-item checks)
    lines = [l.strip() for l in review_text.splitlines() if l.strip()]
    final_line = lines[-1] if lines else ""

    if final_line.startswith(_EXPERT_REVIEW_ITERATE):
        concern = final_line[len(_EXPERT_REVIEW_ITERATE):].strip()
        prompt = f"{prompt}\n\nEXPERT_REVIEW (iterate): {concern}"
        return prompt, "iterate"

    if final_line.startswith(_EXPERT_REVIEW_SURFACE):
        concern = final_line[len(_EXPERT_REVIEW_SURFACE):].strip()
        prompt = f"{prompt}\n\nEXPERT_REVIEW (surface): {concern}"
        return prompt, "surface"

    # EXPERT_APPROVED or unrecognised → approved
    return prompt, "approved"
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Pre-action fires for non-critique-enabled tools when a hat is active (consistency):
   ```bash
   python3.11 -c "
   import inspect, skillforge.forge as f
   src = inspect.getsource(f.Forge.run_turn)
   # pre-action call must not be inside 'if spec.critique_enabled'
   pre_pos = src.index('_run_expert_pre_action')
   # find the surrounding if-block context — simplest check: critique_enabled must
   # not appear on the same line as the pre_action call
   lines = src.splitlines()
   for i, line in enumerate(lines):
       if '_run_expert_pre_action' in line and 'critique_enabled' in line:
           raise AssertionError(f'Pre-action gated on critique_enabled at line {i}')
   print('consistency OK')
   "
   ```

3. `TurnEvent` types `expert_pre_action` and `expert_post_review` are emitted:
   ```bash
   grep '"expert_pre_action"\|"expert_post_review"\|expert_pre_action\|expert_post_review' skillforge/forge.py | grep "type=" | wc -l
   # must be ≥ 2
   ```

4. Log markers are distinctive:
   ```bash
   grep "\[EXPERT_PRE_ACTION\]\|\[EXPERT_POST_REVIEW\]" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

5. Review prompt requires per-item checking (anti-rubber-stamp):
   ```bash
   grep "rubber-stamp\|every item\|PASS or FAIL\|each item" skillforge/forge.py
   # must match
   ```

6. Structured pre-action prompt sections are present:
   ```bash
   grep "KNOWN FACTS:\|GAPS:\|EXPERT ASSESSMENT:\|SUB-AGENT INSTRUCTIONS:" skillforge/forge.py
   # must match all 4
   ```

7. No-hat path still works:
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
p39d: harden reasoning loop — structured prompts, consistent firing, TurnEvents, anti-rubber-stamp review
```

Branch: `claude/p39d` (from main, after p39a–p39c are merged). Push when done.

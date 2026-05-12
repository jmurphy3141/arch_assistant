# Task p40a: Pre-Action Section-Header Validation

## Objective

The existing shallow-response guard in `_run_expert_pre_action()` checks total
character count but not structure. A 300-char wall of text with no section
headers satisfies the char guard while completely bypassing the structured
4-section format. Add a second guard that validates the four required headers
are present before accepting the response.

---

## Scope

**Touch:**
- `skillforge/forge.py` — update `_run_expert_pre_action()`

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_EXPERT_PRE_ACTION_HEADERS\|Missing sections" skillforge/forge.py  # must be zero
```

---

## What to Add

### Module-level constant (near the other `_EXPERT_*` constants)

```python
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT INSTRUCTIONS:",
)
```

### Header check in `_run_expert_pre_action()`

Add **after** the shallow-response guard (after the `if len(reasoning) < _EXPERT_THINKING_MIN_CHARS:` warning block) and **before** the `NEEDS_CLARIFICATION:` check.

Only applies when reasoning is not a clarification request:

```python
    # Section-header guard: all 4 required sections must be present.
    if not reasoning.startswith("NEEDS_CLARIFICATION:"):
        missing = [h for h in _EXPERT_PRE_ACTION_HEADERS if h not in reasoning]
        if missing:
            logger.warning(
                "[EXPERT_PRE_ACTION] Missing sections %s for tool '%s' session=%s — retrying",
                missing, tool_name, session_id,
            )
            missing_list = ", ".join(missing)
            header_retry_prompt = (
                f"{pre_action_prompt}\n\n"
                f"[Your response is missing required sections: {missing_list}. "
                "You MUST include all four labeled sections exactly as shown: "
                "KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT INSTRUCTIONS:. "
                "Rewrite with all four sections present.]"
            )
            try:
                raw = await self._text_runner(
                    header_retry_prompt, system_msg, "expert_pre_action_header_retry"
                )
                reasoning = raw.strip()
            except Exception:
                logger.exception(
                    "[EXPERT_PRE_ACTION] Header retry failed session=%s tool=%s",
                    session_id, tool_name,
                )
            still_missing = [h for h in _EXPERT_PRE_ACTION_HEADERS if h not in reasoning]
            if still_missing:
                logger.warning(
                    "[EXPERT_PRE_ACTION] Still missing sections %s after retry session=%s tool=%s",
                    still_missing, session_id, tool_name,
                )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Constant is defined with all 4 headers:
   ```bash
   grep "_EXPERT_PRE_ACTION_HEADERS" skillforge/forge.py | wc -l
   # must be ≥ 2 (definition + use)
   ```

3. Header retry marker is present:
   ```bash
   grep "expert_pre_action_header_retry\|Missing sections" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

4. No-hat path still works:
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

5. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p40a: pre-action section-header validation with retry
```

Branch: `claude/p40a` (from main). Push when done.

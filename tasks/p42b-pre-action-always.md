# Task p42b: Lightweight Fallback Pre-Action for Unhatted Tools

## Objective

`_run_expert_pre_action()` fires only when expert hats are active. Tools
registered without `requires_hat` skip all pre-action reasoning entirely.
This violates the "think before any tool call" requirement.

Add a `pre_action_always: bool = False` constructor parameter to Forge. When
enabled, any domain tool call that would otherwise skip pre-action (no expert
hats active) gets a lightweight fallback reasoning call instead — a short
structured check, not the full 4-section expert analysis.

This ensures Forge consistently thinks before acting regardless of hat state,
while keeping the overhead proportional (light check for unhatted tools, full
expert analysis for hatted tools).

---

## Scope

**Touch:**
- `skillforge/forge.py` — add `pre_action_always` constructor param, add
  `_run_pre_action_light()` method, wire into `run_turn()` domain dispatch

**Do NOT touch:** `registry.py`, hat files, `archie_wiring.py`, tests, other modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "pre_action_always\|_run_pre_action_light\|PRE_ACTION_LIGHT" skillforge/forge.py
# must be zero
```

---

## Changes

### 1. Add `pre_action_always` constructor parameter

In `Forge.__init__`, add:

```python
def __init__(
    self,
    *,
    base_system_prompt: str,
    hat_engine: ...,
    memory: ...,
    text_runner: ...,
    ...
    step3_planning: bool = False,
    pre_action_always: bool = False,   # ← new
) -> None:
    ...
    self._pre_action_always = pre_action_always
```

### 2. Add `_run_pre_action_light()` method

Add near `_run_expert_pre_action()`:

```python
async def _run_pre_action_light(
    self,
    *,
    prompt: str,
    tool_name: str,
    tool_args: dict,
    session_id: str,
    events: list,
) -> str:
    """
    Lightweight fallback pre-action reasoning for domain tools with no active
    expert hat. Asks the manager to briefly confirm the tool choice and args
    are correct before dispatch.

    Returns the updated prompt. No-op on exception (returns prompt unchanged).
    """
    light_prompt = (
        f"{prompt}\n\n"
        "╔══════════════════════════════════╗\n"
        "║  PRE-ACTION CHECK                ║\n"
        "╚══════════════════════════════════╝\n"
        f"You are about to call '{tool_name}' with args: {tool_args}\n\n"
        "Before dispatching, briefly confirm:\n"
        "GOAL CHECK: Does this tool directly address the user's current goal?\n"
        "ARGS CHECK: Are the arguments complete and correct?\n"
        "RISK CHECK: Is there any obvious risk or missing information?\n\n"
        "Write 1-2 sentences per check. Output plain text — do NOT call a tool here."
    )
    system_msg = self._get_system_msg()

    try:
        raw = await self._text_runner(light_prompt, system_msg, "pre_action_light")
    except Exception:
        logger.exception(
            "[PRE_ACTION_LIGHT] Call failed session=%s tool=%s", session_id, tool_name
        )
        return prompt

    reasoning = raw.strip()
    if not reasoning:
        return prompt

    if len(reasoning) < 50:
        logger.warning(
            "[PRE_ACTION_LIGHT] Shallow response (%d chars) session=%s tool=%s",
            len(reasoning), session_id, tool_name,
        )

    logger.info(
        "[PRE_ACTION_LIGHT] session=%s tool=%s:\n%s", session_id, tool_name, reasoning
    )
    events.append(
        TurnEvent(
            type="pre_action_light",
            message=f"Pre-action check for '{tool_name}'",
            data={"tool": tool_name, "reasoning": reasoning},
        )
    )
    return f"{prompt}\n\nPRE_ACTION_CHECK ({tool_name}):\n{reasoning}"
```

### 3. Wire into `run_turn()` domain dispatch

In the domain tool dispatch section, after the `requires_hat` auto-activation
block and **before** the `_run_expert_pre_action` call, add:

```python
            # Lightweight fallback pre-action for tools with no expert hat
            expert_hats_active = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
            if self._pre_action_always and not expert_hats_active:
                prompt = await self._run_pre_action_light(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    session_id=session_id,
                    events=events,
                )
```

Note: if expert hats ARE active, `_run_expert_pre_action` handles it (full
4-section analysis). The light fallback only fires when no expert hats are active.

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. New symbols present:
   ```bash
   grep "pre_action_always\|_run_pre_action_light\|PRE_ACTION_LIGHT\|pre_action_light" skillforge/forge.py | wc -l
   # must be ≥ 4
   ```

3. No-hat path unaffected (`pre_action_always=False` default):
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
       def apply_hat(self, hats, name): return hats + [name]
       def drop_hat(self, hats, name): return [h for h in hats if h != name]
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
   light_events = [e for e in result.events if e.type == 'pre_action_light']
   assert len(light_events) == 0, 'pre_action_always=False should not fire'
   print('default path OK')
   "
   ```

4. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p42b: pre_action_always — lightweight fallback pre-action for unhatted tools
```

Branch: `claude/p42b` (from main, after p42a merged). Push when done.

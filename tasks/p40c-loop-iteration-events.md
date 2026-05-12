# Task p40c: Loop-Iteration TurnEvents

## Objective

The `run_turn()` loop emits `TurnEvent` for hat activations (`hat_activate`),
hat drops (`hat_drop`), expert pre-action thinking (`expert_pre_action`), and
expert post-review (`expert_post_review`). There is no event marking the start
of each iteration. Callers cannot observe "we're on iteration N with hats X"
without parsing log files.

Emit a lightweight `loop_iteration` TurnEvent at the top of each loop
iteration so the full turn execution is observable.

---

## Scope

**Touch:**
- `skillforge/forge.py` — add one `events.append(...)` call in `run_turn()`

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "loop_iteration" skillforge/forge.py  # must be zero
```

---

## Change

In `run_turn()`, inside the `for iteration in range(self._max_iterations):` loop,
**after** the stale-hat warning block and **before** the per-round prompt
enrichment, add:

```python
            events.append(
                TurnEvent(
                    type="loop_iteration",
                    message=(
                        f"Iteration {iteration + 1}/{self._max_iterations}"
                        + (f" — hats: {', '.join(active_hats)}" if active_hats else "")
                    ),
                    data={"iteration": iteration, "active_hats": list(active_hats)},
                )
            )
```

The exact insertion point in context:

```python
        for iteration in range(self._max_iterations):

            # Stale-hat warning (no side effect — caller logs if desired)
            for h in active_hats:
                hat_rounds[h] = hat_rounds.get(h, 0) + 1
            stale = self._hat_engine.warn_stale_hats(active_hats, hat_rounds)
            if stale:
                logger.warning(
                    "Stale hats active > 5 rounds: %s session=%s", stale, session_id
                )

            # ── NEW: loop-iteration visibility event ──────────────────────────
            events.append(
                TurnEvent(
                    type="loop_iteration",
                    message=(
                        f"Iteration {iteration + 1}/{self._max_iterations}"
                        + (f" — hats: {', '.join(active_hats)}" if active_hats else "")
                    ),
                    data={"iteration": iteration, "active_hats": list(active_hats)},
                )
            )

            # Per-round prompt enrichment ...
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Event type is present:
   ```bash
   grep "loop_iteration" skillforge/forge.py | wc -l
   # must be ≥ 2 (the string appears in the type and in the message)
   ```

3. The event fires on the no-hat path (events list is non-empty after a plain turn):
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
   loop_events = [e for e in result.events if e.type == 'loop_iteration']
   assert len(loop_events) >= 1, f'Expected loop_iteration event, got: {result.events}'
   print(f'loop_iteration event OK — {loop_events[0].message}')
   "
   ```

4. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p40c: emit loop_iteration TurnEvent each iteration for full loop observability
```

Branch: `claude/p40c` (from main, after p40a–p40b merged). Push when done.

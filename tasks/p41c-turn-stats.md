# Task p41c: Turn Stats — Reasoning Loop Observability

## Objective

`TurnResult.events` contains every reasoning loop event, but callers have to
iterate them manually to understand what happened in a turn. Add a `stats` dict
to `TurnResult` that summarises the turn at a glance, and log it at INFO at
turn end. This makes the reasoning loop observable without log-parsing.

---

## Scope

**Touch:**
- `skillforge/types.py` — add `stats` field to `TurnResult`
- `skillforge/forge.py` — populate `stats` before returning `TurnResult`

**Do NOT touch:** hat files, skill files, other Python modules, tests.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py skillforge/types.py
grep "stats" skillforge/types.py skillforge/forge.py  # must be zero
```

---

## Changes

### 1. Add `stats` to `TurnResult` in `skillforge/types.py`

```python
@dataclass
class TurnResult:
    """
    Return value of Forge.run_turn().
    reply is the Markdown string to show the user.
    """
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    history_length: int = 0
    events: list[TurnEvent] = field(default_factory=list)
    stats: dict = field(default_factory=dict)   # ← new
```

### 2. Populate `stats` in `run_turn()` before the `return TurnResult(...)` call

Add this block immediately before `return TurnResult(...)`:

```python
        # Build turn stats from emitted events.
        _event_counts: dict[str, int] = {}
        for _e in events:
            _event_counts[_e.type] = _event_counts.get(_e.type, 0) + 1

        _review_events = [e for e in events if e.type == "expert_post_review"]
        _review_decisions = [e.data.get("decision", "") for e in _review_events]

        turn_stats = {
            "iterations": iteration + 1 if reply else self._max_iterations,
            "active_hats": list(active_hats),
            "tool_calls": len(tool_calls),
            "event_counts": _event_counts,
            "review_approved": _review_decisions.count("approved"),
            "review_iterate": _review_decisions.count("iterate"),
            "review_surface": _review_decisions.count("surface"),
            "clarifications_needed": sum(
                1 for e in events
                if e.type == "expert_pre_action"
                and "clarification" in e.data
            ),
        }
        logger.info(
            "[TURN_STATS] session=%s iterations=%d hats=%s tools=%d "
            "review=approved:%d/iterate:%d/surface:%d",
            session_id,
            turn_stats["iterations"],
            turn_stats["active_hats"] or "none",
            turn_stats["tool_calls"],
            turn_stats["review_approved"],
            turn_stats["review_iterate"],
            turn_stats["review_surface"],
        )
```

Then pass it into `TurnResult`:

```python
        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            artifacts=artifacts,
            history_length=len(history or []) + 1,
            events=events,
            stats=turn_stats,
        )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/types.py skillforge/forge.py
   ```

2. `stats` field exists on `TurnResult`:
   ```bash
   grep "stats" skillforge/types.py | wc -l
   # must be ≥ 1
   ```

3. Log marker is present:
   ```bash
   grep "TURN_STATS" skillforge/forge.py | wc -l
   # must be ≥ 1
   ```

4. `stats` is populated on a plain turn:
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
   assert 'iterations' in result.stats
   assert 'event_counts' in result.stats
   assert result.stats['tool_calls'] == 0
   print('stats OK:', result.stats)
   "
   ```

5. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p41c: TurnResult.stats + TURN_STATS log — per-turn reasoning loop observability
```

Branch: `claude/p41c` (from main, after p41a–p41b merged). Push when done.

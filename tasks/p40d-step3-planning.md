# Task p40d: Step 3 Planning Call — Hat-Selection Reasoning

## Objective

Steps 1–3 of the manager reasoning loop (understand request → memory
assessment → plan + hat selection) happen entirely inside the main LLM call
with no forced structure and no observable output. The LLM may pick the wrong
hat or skip hat activation because there was no dedicated thinking step.

Add a `_run_step3_planning()` method that fires once at the start of
`run_turn()`, before the main loop. It asks the manager to explicitly reason
through Steps 1–3, outputs the result as a `step3_planning` TurnEvent, and
appends `STEP3_PLANNING:` to the prompt so the main loop benefits from that
reasoning.

**Cost tradeoff:** This adds one LLM call per turn. It is gated on a
`Forge` constructor parameter (`step3_planning: bool = True`) so it can be
disabled if latency is a concern.

---

## Scope

**Touch:**
- `skillforge/forge.py` — add `_run_step3_planning()` method, wire into
  `run_turn()`, add `step3_planning` constructor param

**Do NOT touch:** hat files, skill files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
grep "_run_step3_planning\|step3_planning" skillforge/forge.py  # must be zero
```

---

## Changes

### 1. Add `step3_planning` constructor parameter

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
        step3_planning: bool = True,
    ):
        ...
        self._step3_planning = step3_planning
```

### 2. Add `_run_step3_planning()` method

Add near `_run_expert_pre_action()`:

```python
async def _run_step3_planning(
    self,
    *,
    prompt: str,
    user_message: str,
    active_hats: list[str],
    memory_snapshot: "MemorySnapshot | None",
    session_id: str,
    events: list,
) -> str:
    """
    Step 3 of the manager reasoning loop: hat-selection planning.

    Fires once at the start of run_turn() before the main loop.
    Asks the manager to reason through Steps 1–3 (understand request,
    assess memory, plan and select hat) and appends the output as
    STEP3_PLANNING to the prompt for the main loop to use.

    Returns the updated prompt. No-op on exception (returns prompt unchanged).
    """
    hat_names = self._hat_engine.get_hat_tool_definitions()
    available = (
        ", ".join(t.get("name", "") for t in hat_names if t.get("name", "").startswith("use_hat_"))
        if hat_names else "(none registered)"
    )
    already_active = (
        f"Currently active hats: {', '.join(active_hats)}." if active_hats
        else "No hats are currently active."
    )

    planning_prompt = (
        f"{prompt}\n\n"
        "╔══════════════════════════════════╗\n"
        "║  STEP 3 — PLANNING               ║\n"
        "╚══════════════════════════════════╝\n"
        "Before entering the execution loop, reason through Steps 1–3:\n\n"
        "STEP 1 — UNDERSTAND:\n"
        "- What is the user's real goal? Name the deliverable "
        "(BOM, diagram, Terraform, POV, JEP, WAF review, or none).\n"
        "- Is this a new request, a revision, or a clarification?\n"
        "- Is anything ambiguous? If so, what is missing?\n\n"
        "STEP 2 — MEMORY ASSESSMENT:\n"
        "- What facts are already confirmed (shapes, region, services, "
        "budget, HA mode, customer name, compliance scope)?\n"
        "- What is missing or unconfirmed?\n"
        "- Is there enough to produce a complete deliverable, or must you ask first?\n\n"
        "STEP 3 — PLAN + HAT SELECTION:\n"
        f"- {already_active}\n"
        f"- Available hats: {available}\n"
        "- Which hat (if any) should you activate for this request, and why?\n"
        "- What is your execution plan?\n\n"
        "Output your reasoning as plain text using the labeled sections above.\n"
        "Do NOT call a tool here."
    )
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(planning_prompt, system_msg, "step3_planning")
    except Exception:
        logger.exception("[STEP3_PLANNING] Call failed session=%s", session_id)
        return prompt

    planning_text = raw.strip()
    if not planning_text:
        return prompt

    logger.info("[STEP3_PLANNING] session=%s:\n%s", session_id, planning_text)
    events.append(
        TurnEvent(
            type="step3_planning",
            message="Step 3 planning — hat selection and execution plan",
            data={"planning": planning_text, "active_hats": list(active_hats)},
        )
    )
    return f"{prompt}\n\nSTEP3_PLANNING:\n{planning_text}"
```

### 3. Wire into `run_turn()`

In `run_turn()`, **after** `memory_snapshot` is assembled and **before** the
`for iteration in range(...)` loop, add:

```python
        # Step 3: hat-selection planning (one LLM call, fires once per turn)
        if self._step3_planning:
            prompt = await self._run_step3_planning(
                prompt=prompt,
                user_message=user_message,
                active_hats=active_hats,
                memory_snapshot=memory_snapshot,
                session_id=session_id,
                events=events,
            )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

2. Method and constructor param exist:
   ```bash
   grep "_run_step3_planning\|step3_planning" skillforge/forge.py | wc -l
   # must be ≥ 4 (definition, call site, constructor, log marker)
   ```

3. Planning steps are present in the prompt:
   ```bash
   grep "STEP 1 — UNDERSTAND\|STEP 2 — MEMORY\|STEP 3 — PLAN" skillforge/forge.py | wc -l
   # must be ≥ 3
   ```

4. No-hat path still works with `step3_planning=False`:
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
       step3_planning=False,
   )
   result = asyncio.run(forge.run_turn(
       session_id='test', user_message='hello', context={}
   ))
   assert result.reply == 'plain reply'
   planning_events = [e for e in result.events if e.type == 'step3_planning']
   assert len(planning_events) == 0, 'step3_planning=False should suppress the call'
   print('step3_planning disabled OK')
   "
   ```

5. With `step3_planning=True` (default), the event fires. Modify the smoke
   test above to use `step3_planning=True` and assert
   `len(planning_events) >= 1`. Note: `null_runner` returns `'plain reply'`
   for every call including the planning call — this is fine for the smoke
   test.

6. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p40d: Step 3 planning call — hat-selection reasoning before main loop
```

Branch: `claude/p40d` (from main, after p40a–p40c merged). Push when done.

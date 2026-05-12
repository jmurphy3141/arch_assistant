# p40 Codex Prompts — Deepen Manager Expert Thinking (Genuine Gaps)

Run in order: p40a → p40b → p40c → p40d. Each task merges to main before
the next starts.

---

## p40a — Pre-Action Section-Header Validation

```
Read tasks/p40a-pre-action-header-validation.md carefully.

Run the prerequisite check first:
  python3.11 -m compileall skillforge/forge.py
  grep "_EXPERT_PRE_ACTION_HEADERS\|Missing sections" skillforge/forge.py

Then implement exactly as specified:
- Add the _EXPERT_PRE_ACTION_HEADERS module-level constant near the other _EXPERT_* constants
- In _run_expert_pre_action(), add the header validation block after the shallow-response guard
  and before the NEEDS_CLARIFICATION check
- The guard checks all 4 headers (KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT INSTRUCTIONS:)
- Retry once with expert_pre_action_header_retry if any are missing; log missing section names

Run ALL acceptance criteria checks from the spec before committing.

Commit message: p40a: pre-action section-header validation with retry
Branch: claude/p40a (from main). Push when done.
```

---

## p40b — Iterate-Aware Pre-Action Context

```
Read tasks/p40b-iterate-context.md carefully.

Prerequisites: p40a is merged to main. Start from main.

Run the prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "iteration.*pre_action\|RETRY CONTEXT\|attempt" skillforge/forge.py

Implement exactly as specified:
- Add iteration: int = 0 parameter to _run_expert_pre_action()
- Build retry_context string when iteration > 0; extract EXPERT_REVIEW (iterate): concern from prompt
- Prepend retry_context to pre_action_prompt (between {prompt} and the box header)
- Update the call site in run_turn() to pass iteration=iteration

Run ALL acceptance criteria checks from the spec before committing.

Commit message: p40b: iteration-aware pre-action — surface retry context and attempt number
Branch: claude/p40b (from main, after p40a merged). Push when done.
```

---

## p40c — Loop-Iteration TurnEvents

```
Read tasks/p40c-loop-iteration-events.md carefully.

Prerequisites: p40a, p40b merged to main. Start from main.

Run the prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "loop_iteration" skillforge/forge.py

Implement exactly as specified:
- In run_turn(), inside the for iteration in range(self._max_iterations): loop,
  after the stale-hat warning block and before per-round prompt enrichment,
  append a TurnEvent(type="loop_iteration", ...)
- Include iteration number, max_iterations, and active_hats in data

Run ALL acceptance criteria checks from the spec including the smoke test that
asserts the loop_iteration event fires on a plain no-hat turn.

Commit message: p40c: emit loop_iteration TurnEvent each iteration for full loop observability
Branch: claude/p40c (from main, after p40a–p40b merged). Push when done.
```

---

## p40d — Step 3 Planning Call

```
Read tasks/p40d-step3-planning.md carefully.

Prerequisites: p40a, p40b, p40c merged to main. Start from main.

Run the prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "_run_step3_planning\|step3_planning" skillforge/forge.py

Implement exactly as specified:
- Add step3_planning: bool = True parameter to Forge.__init__; store as self._step3_planning
- Add _run_step3_planning() method with 3-section prompt (STEP 1 UNDERSTAND,
  STEP 2 MEMORY ASSESSMENT, STEP 3 PLAN + HAT SELECTION)
- Wire into run_turn(): call _run_step3_planning() once before the main loop,
  gated on self._step3_planning
- Emits TurnEvent(type="step3_planning", ...)
- Appends STEP3_PLANNING: block to prompt

Run ALL acceptance criteria checks including BOTH smoke tests (step3_planning=False
suppresses the call; step3_planning=True fires it).

Commit message: p40d: Step 3 planning call — hat-selection reasoning before main loop
Branch: claude/p40d (from main, after p40a–p40c merged). Push when done.
```

# Phase 3.9 Plan — Enhanced Manager Reasoning Loop

## Overall Approach

The current Forge ReAct loop is reactive: it builds the system prompt with the
active expert block, then hands off to the LLM in one shot. The manager does not
explicitly reason through its own situation before acting, and does not
self-review as the expert after a tool returns — it relies entirely on the
post-hoc critic hat.

Phase 3.9 adds a **structured 6-step reasoning loop** through three layers:

1. **Skill file** (`skills/manager_reasoning_loop.md`): injected every turn,
   gives the LLM explicit step-by-step instructions for how to reason.
2. **Hat file additions** (`## Pre-Action Checklist` + `## Post-Action Review`):
   domain-specific prerequisites and self-checks injected via the `[ACTIVE EXPERT]`
   block automatically — no Python required.
3. **Forge structured turn** (`skillforge/forge.py`): before the main ReAct loop,
   run a lightweight "planning call" that produces a structured Step 1–3 plan and
   gates tool execution; after tool execution, run an expert self-review from the
   hat's `Post-Action Review` section before the critic hat fires.

---

## The 6-Step Reasoning Loop

```
Step 1: Understand        — Clarify user's real goal and intent
Step 2: Assess Memory     — What is known / unknown from context
Step 3: Plan + Select Hat — Best approach, which hat to wear
Step 4: Expert Pre-Action — Think deeply as the expert; verify prerequisites
Step 5: Execute           — Call sub-agent or tool
Step 6: Expert Review     — Review result while still wearing the hat
         └─ Critic hat fires only after expert self-review approves
```

---

## Task Breakdown

| Task | Files | Description |
|------|-------|-------------|
| p39a | `skills/manager_reasoning_loop.md` (new) | 6-step loop skill injected every turn |
| p39b | all 8 hat files | Add `## Pre-Action Checklist` + `## Post-Action Review` to each hat |
| p39c | `skillforge/forge.py` | Planning call (Steps 1–3) + expert self-review (Step 6) |

**Run order:** p39a + p39b in parallel → p39c after both complete.

---

## Acceptance Criteria (all tasks)

1. `skills/manager_reasoning_loop.md` exists and references all 6 steps.
2. All 8 hat files have `## Pre-Action Checklist` and `## Post-Action Review`.
3. `python3.11 -m compileall skillforge/forge.py` exits 0.
4. Planning call fires before the first ReAct iteration when hats are active:
   ```bash
   grep "planning_call\|_run_planning\|Step 1\|Step 2\|Step 3" skillforge/forge.py
   ```
5. Expert self-review fires after a tool returns and before the critic pass:
   ```bash
   grep "_run_expert_review\|post_action\|Post-Action" skillforge/forge.py
   ```
6. `pytest tests/test_forge.py -q --tb=short` — same pass count.

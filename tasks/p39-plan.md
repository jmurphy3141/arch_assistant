# Phase 3.9 Plan — Enhanced Manager Reasoning Loop

## Core Principle

**Hats belong to the manager (Archie / Forge), not the sub-agents.**

When the manager activates an expert hat, it becomes the expert. It reasons,
plans, and critiques as that expert. Sub-agents are execution engines — they
receive precise instructions from an expert manager and return raw results.
The manager then reviews those results *as the expert* before surfacing them.

The current Forge loop lacks explicit expert thinking. The manager routes to
tools but does not reason deeply as the expert before or after. This means:
- Shallow tool calls with missing context
- Sub-agent output accepted without expert-level review
- Hat activation is cosmetic — it changes the prompt but not the thinking

Phase 3.9 makes expert thinking explicit through a mandatory 6-step loop.

---

## The 6-Step Manager Reasoning Loop

```
Step 1: Understand          — Clarify the user's real goal and intent
Step 2: Assess Memory       — What is known / unknown from context
Step 3: Plan + Select Hat   — Decide approach; activate the right hat
Step 4: Expert Pre-Action   — Manager THINKS AS THE EXPERT before calling sub-agent:
                               known facts, gaps, approach, and precise instructions
Step 5: Execute             — Call sub-agent or tool with expert-crafted args
Step 6: Expert Post-Review  — Manager REVIEWS AS THE EXPERT after sub-agent returns:
                               quality bar check, consistency, gaps, iterate or surface
                └─ Critic hat fires only after expert post-review approves
```

Steps 4 and 6 are the new mandatory expert thinking passes. They both use the
full `[ACTIVE EXPERT]` system prompt — the manager is genuinely wearing the hat
during these calls, not delegating the thinking to the sub-agent.

---

## What Changes

| Layer | What changes |
|-------|-------------|
| `skills/manager_reasoning_loop.md` | Documents the 6-step loop; injected every turn so the LLM follows it |
| All 8 hat files | Add `## Pre-Action Checklist` + `## Post-Action Review` so Steps 4/6 have domain-specific checklists |
| `skillforge/forge.py` | `_run_expert_pre_action()` (Step 4) + `_run_expert_post_review()` (Step 6) wired into the ReAct loop |

---

## Task Breakdown

| Task | Files | Description | Run order |
|------|-------|-------------|-----------|
| p39a | `skillforge/forge.py` + `skills/manager_reasoning_loop.md` | Define + implement the full 6-step loop | First |
| p39b | All 8 hat files | Add `## Pre-Action Checklist` + `## Post-Action Review` sections | After p39a |
| p39c | `skillforge/forge.py` | Strengthen Step 6: post-review decides iterate vs surface; log expert reasoning | After p39b |

**Run order: p39a → p39b → p39c** (sequential; each task builds on the previous).

---

## Acceptance Criteria (all tasks)

1. `skills/manager_reasoning_loop.md` exists and explicitly names all 6 steps.
2. All 8 hat files have `## Pre-Action Checklist` and `## Post-Action Review`.
3. `python3.11 -m compileall skillforge/forge.py` exits 0.
4. `_run_expert_pre_action` fires before every critique-enabled tool call when a hat is active:
   ```bash
   grep "_run_expert_pre_action" skillforge/forge.py
   ```
5. `_run_expert_post_review` fires after every critique-enabled tool call, before the critic:
   ```bash
   grep "_run_expert_post_review\|_run_critique_pass" skillforge/forge.py
   ```
6. Expert reasoning is logged at INFO level (search for `logger.info.*expert` or `EXPERT`).
7. `pytest tests/test_forge.py -q --tb=short` — same pass count as baseline.

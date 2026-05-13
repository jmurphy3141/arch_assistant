# SkillForge Framework — Deliberate Pre-Action Thinking Requirements

**Date:** May 8, 2026
**Owner:** Jason Murphy (PM)
**Status:** Active

## Objective

Make Forge (the core orchestrator) think deliberately and strategically
**before** taking any major action — similar to how Grok and Claude reason
before responding.

Forge must remain **manager-agnostic** (no Archie/OCI assumptions in the
core). All thinking behavior should be driven by the manager's system prompt
and hat skills.

## Core Principle

"Think before you act" must be a fundamental behavior of Forge, not an
optional or prompt-only suggestion.

## Requirements

### 1. Mandatory Pre-Action Reasoning Step

- Before calling any tool, delegating to a sub-agent, or activating a hat,
  Forge must execute a short but structured internal reasoning step.
- This step must be enforced in code (not just hoped for in the prompt).
- The reasoning must consider:
  - Current user request / goal
  - Relevant memory/context
  - Available hats and when to use them
  - Best next action and potential risks

### 2. Keep Forge Manager-Agnostic

- The core reasoning mechanism must be generic and reusable for any
  domain/manager.
- No hard-coded Archie, OCI, or BOM-specific logic in `skillforge/`.
- The manager's system prompt and active hat skills should control the
  *style* and *depth* of thinking.

### 3. Lightweight Implementation

- Do not add heavy chains of thought or excessive steps that slow down
  responses significantly.
- Use simple guards (minimum reasoning length, required section headers,
  retry on poor reasoning).
- Keep changes minimal and clean.

### 4. Visibility

- All pre-action reasoning must be clearly logged (e.g. `[PRE_ACTION_REASONING]`).
- Show a short "Thinking..." status message to the user for transparency.

## Success Criteria

- Forge consistently thinks before acting.
- The thinking step is visible in logs.
- Different managers (Archie, AWS Manager, etc.) can customize thinking depth
  via their system prompt and hats.
- No significant performance regression.
- Code remains clean and manager-agnostic.

## Implementation Status

| Requirement | Implementation | Phase |
|---|---|---|
| Pre-loop planning call (Step 3) | `_run_step3_planning()` in `forge.py` | p40d |
| Section-header validation on planning output | `_STEP3_PLANNING_HEADERS` + retry | p42a |
| Expert pre-action before domain tools | `_run_expert_pre_action()` in `forge.py` | p40a/p40b |
| Code gate: hat required before tool dispatch | `requires_hat` on `register_tool()` | p42a |
| Expert post-review after tool result | `_run_expert_post_review()` in `forge.py` | p39c |
| Iterate correction directive | `CORRECTION REQUIRED` block | p41b |
| Lightweight fallback for tools without hat | `pre_action_always` param | **p42b** |
| "Thinking..." visibility in chat UI | TurnEvent → SSE → UI | **p42c** |

## Non-Goals

- Complex multi-step visible reasoning to the user
- Code bloat or new complex classes
- Tying Forge to any specific domain (including Archie)

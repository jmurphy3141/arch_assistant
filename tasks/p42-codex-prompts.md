# p42 Codex Prompts — Structural Thinking Enforcement

## Background

Forge is a domain-agnostic ReAct orchestrator. Expert reasoning (pre-action
and post-review) is gated on expert hats being active. Hats were previously
activated only by the manager LLM — if it skipped that step, all expert
reasoning silently no-oped. p42a adds a code gate: tools registered with
`requires_hat=` cannot dispatch without that hat active. Forge activates it
automatically in code if the LLM forgot.

---

Run in order: p42a → p42b → p42c. Each task merges to main before the next starts.

---

## p42a — `requires_hat` Gate + Step 3 Planning Header Validation

```
Read tasks/p42a-requires-hat-gate.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main. The working branch is behind main on
forge.py. You MUST start from main.

  git fetch origin
  git checkout -b claude/p42a origin/main

Run the prerequisite check first:
  python3.11 -m compileall skillforge/registry.py skillforge/forge.py agent/archie_wiring.py
  grep "requires_hat\|hat_auto_activated\|_STEP3_PLANNING_HEADERS" skillforge/registry.py skillforge/forge.py agent/archie_wiring.py
  # must be zero matches

Then run: ls agent/hats/
Verify the exact hat filenames (without .md extension) match the requires_hat
values in the spec before touching archie_wiring.py.

Implement in this order:
1. skillforge/registry.py — add requires_hat to ToolSpec dataclass and
   ToolRegistry.register() signature and ToolSpec construction
2. skillforge/forge.py — three sub-changes:
   a. Add requires_hat to Forge.register_tool() signature and registry call
   b. Add auto-activation block in run_turn() domain dispatch — place it
      AFTER the unknown-tool check and BEFORE any _run_expert_pre_action call
      (search for the comment "# ── Domain tool" to find the right location)
   c. Add _STEP3_PLANNING_HEADERS constant near other _EXPERT_* constants,
      and add header validation + retry in _run_step3_planning() after
      planning_text = raw.strip() and before logger.info
3. agent/archie_wiring.py — add requires_hat= to all six domain tool
   registrations (generate_bom, generate_diagram, generate_terraform,
   generate_pov, generate_jep, generate_waf). Leave save_notes,
   get_summary, get_document unchanged.

Run ALL seven acceptance criteria checks from the spec before committing,
including both smoke tests (no-hat path and auto-activation).

Commit message:
p42a: requires_hat gate — Forge auto-activates expert hats before domain tools

Branch: claude/p42a (from main). Push when done.
```

---

## p42b — Lightweight Fallback Pre-Action for Unhatted Tools

```
Read tasks/p42b-pre-action-always.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main after p42a is merged.

  git fetch origin
  git checkout -b claude/p42b origin/main

Run the prerequisite check first:
  python3.11 -m compileall skillforge/forge.py
  grep "pre_action_always\|_run_pre_action_light\|PRE_ACTION_LIGHT" skillforge/forge.py
  # must be zero matches

Implement in this order:
1. Add pre_action_always: bool = False to Forge.__init__ signature and
   store as self._pre_action_always
2. Add _run_pre_action_light() method near _run_expert_pre_action()
3. In run_turn() domain dispatch, after the requires_hat auto-activation
   block and before _run_expert_pre_action, add the fallback firing block
   that checks self._pre_action_always and not expert_hats_active

Run ALL acceptance criteria checks including the smoke test that verifies
pre_action_always=False (default) fires no pre_action_light events.

Commit message:
p42b: pre_action_always — lightweight fallback pre-action for unhatted tools

Branch: claude/p42b (from main, after p42a merged). Push when done.
```

---

## p42c — "Thinking..." Visibility in Chat UI

```
Read tasks/p42c-thinking-status-events.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main after p42a and p42b are merged.

  git fetch origin
  git checkout -b claude/p42c origin/main

Run the prerequisite check first:
  grep "thinking\|step3_planning\|expert_pre_action\|pre_action_light" agent/chat_stream.py
  grep "event_type.*thinking\|thinking.*event" ui/src/components/ChatInterface.tsx
  # must be zero matches

This task touches two files:
1. agent/chat_stream.py — in the post-turn TurnEvent loop, add cases for
   step3_planning, expert_pre_action, expert_post_review, hat_auto_activated,
   pre_action_light to yield a "thinking" event_type dict with a label string
2. ui/src/components/ChatInterface.tsx — handle event_type "thinking" to
   show a muted status line with the label; clear it on "completion"

Read agent/chat_stream.py lines 86-106 carefully to understand the existing
event loop structure before adding to it.
Read ui/src/components/ChatInterface.tsx to find where event_type is handled
before adding the thinking case.

Run ALL acceptance criteria checks including TypeScript compile check.

Commit message:
p42c: surface reasoning TurnEvents as "Thinking..." status in chat UI

Branch: claude/p42c (from main, after p42a–p42b merged). Push when done.
```

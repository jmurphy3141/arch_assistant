# p42 Codex Prompts — Structural Thinking Enforcement

## Background

Forge is a domain-agnostic ReAct orchestrator. Expert reasoning (pre-action
and post-review) is gated on expert hats being active. Hats were previously
activated only by the manager LLM — if it skipped that step, all expert
reasoning silently no-oped. p42a adds a code gate: tools registered with
`requires_hat=` cannot dispatch without that hat active. Forge activates it
automatically in code if the LLM forgot.

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

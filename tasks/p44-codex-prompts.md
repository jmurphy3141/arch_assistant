# p44 Codex Prompts — Route All Archie Requests Through Forge

## Background

`archie_loop.py` has its own 4,287-line routing engine that intercepts BOM,
diagram, WAF, Terraform, POV, and JEP requests and dispatches them directly to
tools via `_invoke_prerouted_tool()`, returning before `forge.run_turn()` at
line 909 is ever reached. The entire p39–p43 reasoning infrastructure
(step3_planning, requires_hat gate, expert pre-action, expert post-review,
reasoning_sink) is dead code on every real request.

Fix: move sequencing rules to the Archie system prompt, delete the bypass
blocks, and add a test that fails if the pattern is re-introduced.

Run order: p44a → p44b → p44c → p44d. Each task branches from main after the
prior one merges.

---

## p44a — Enrich Archie System Prompt

```
Read tasks/p44a-enrich-archie-system-prompt.md carefully end to end before
touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p44a origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/archie_wiring.py
  grep "BOM before\|diagram before\|POV.*JEP\|sequencing\|prerequisite" \
    agent/archie_wiring.py
  # must be zero matches

Read agent/archie_wiring.py fully before editing — find the base_system_prompt
or ORCHESTRATOR_SYSTEM_MSG string passed to Forge(base_system_prompt=...) and
understand its current structure before appending.

Implement: append the ## Tool Sequencing Rules section from the task spec to
the system prompt string.

Run ALL acceptance criteria checks before committing.

Commit message:
p44a: enrich Archie system prompt — tool sequencing rules for BOM→diagram→WAF order

Branch: claude/p44a (from main). Push when done.
```

---

## p44b — Remove workflow_plan Bypass

```
Read tasks/p44b-remove-workflow-bypass.md carefully end to end before
touching any code.

IMPORTANT: Branch from origin/main AFTER p44a is merged.

  git fetch origin
  git checkout -b claude/p44b origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/archie_loop.py
  grep -n "workflow_plan\|paired_bom_diagram_plan\|_generation_workflow_plan\|_bom_diagram_pair_plan" \
    agent/archie_loop.py | wc -l
  # note the count

Read agent/archie_loop.py lines 600–820 carefully before editing to understand
the full scope of both blocks and their helper functions.

Implement:
1. Delete the workflow_plan block (approx lines 606–717)
2. Delete the paired_bom_diagram_plan block (approx lines 719–811)
3. Delete now-unreferenced helper functions — verify each has no other callers
   before deleting

Do NOT delete _run_generation_step() or _invoke_prerouted_tool().

Run ALL acceptance criteria checks before committing.

Commit message:
p44b: remove workflow_plan bypass — BOM/diagram/WAF requests now route through Forge

Branch: claude/p44b (from main, after p44a merged). Push when done.
```

---

## p44c — Remove parallel_tools Bypass

```
Read tasks/p44c-remove-parallel-bypass.md carefully end to end before
touching any code.

IMPORTANT: Branch from origin/main AFTER p44b is merged.

  git fetch origin
  git checkout -b claude/p44c origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/archie_loop.py
  grep -n "parallel_tools\|parallel_executed\|asyncio.gather" \
    agent/archie_loop.py | wc -l
  # note the count

Read agent/archie_loop.py lines 810–910 carefully before editing.

Implement:
1. Delete the parallel_tools detection and dispatch block
2. Delete the parallel_executed guard before forge.run_turn()
3. Delete parallel_executed = False initialisation
4. Delete now-unreferenced helper functions

Run ALL acceptance criteria checks before committing.

Commit message:
p44c: remove parallel_tools bypass — POV/JEP/BOM parallel dispatch now handled by Forge

Branch: claude/p44c (from main, after p44b merged). Push when done.
```

---

## p44d — Architecture Guard

```
Read tasks/p44d-architecture-guard.md carefully end to end before touching
any files.

IMPORTANT: Branch from origin/main AFTER p44a–p44c are merged.

  git fetch origin
  git checkout -b claude/p44d origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/archie_loop.py
  grep "archie_session" CLAUDE.md | wc -l  # must be 0
  ls tests/test_archie_forge_wiring.py 2>/dev/null && echo EXISTS || echo MISSING
  # must be MISSING

Implement in this order:
1. Delete the test case test_prerouting_bom_uses_invoke_tool from
   tests/test_archie_loop_invoke_tool.py — it asserts forge.run_turn is NOT
   called for BOM requests (the old bypass behaviour). p44c made this obsolete
   and it will conflict with the new wiring test.
2. git mv agent/archie_loop.py agent/archie_session.py
3. grep -rn "archie_loop" --include="*.py" . to find all import sites; update each
4. Add the rule to CLAUDE.md under "Known Debt — Do Not Make Worse"
5. Create tests/test_archie_forge_wiring.py with the parametrized test from
   the task spec

Run ALL acceptance criteria checks before committing.

Commit message:
p44d: architecture guard — CLAUDE.md rule, forge-wiring test, rename to archie_session.py

Branch: claude/p44d (from main, after p44a–p44c merged). Push when done.
```

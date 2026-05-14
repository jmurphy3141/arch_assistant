# Requirements: p44 — Route All Archie Requests Through Forge

## Problem

`archie_loop.py` (4,287 lines) was built before Forge existed. It has its own
routing engine that intercepts ~95% of user requests — BOM, diagram, WAF,
Terraform, POV, JEP — and dispatches them directly to tools via
`_invoke_prerouted_tool()`, returning before `forge.run_turn()` at line 909 is
ever reached.

The result: p39–p43's entire reasoning infrastructure (step3_planning,
requires_hat gate, expert pre-action, expert post-review, correction
propagation, reasoning_sink) is dead code on every real request.

## Correct Architecture

```
User message
  │
  ▼
archie_session (thin wrapper, ~80 lines)
  - load history, context
  - call forge.run_turn()
  - save history, context, artifacts
  - return result
  │
  ▼
Forge.run_turn()   ← owns ALL orchestration
  - step3_planning
  - requires_hat gate (auto-activates expert hat)
  - expert pre-action
  - tool dispatch → BomHandler / DiagramHandler / etc.
  - expert post-review
  - correction propagation on iterate
  │
  ▼
Archie system prompt   ← owns ALL sequencing rules
  - "Generate BOM before diagram when both requested"
  - "Diagram must exist before WAF or Terraform"
  - "POV and JEP can be requested together"
  - "Add AI/ML nodes when user mentions AI features"
```

**Forge = framework. Archie = personality (system prompt + tools + hats).**
archie_loop.py must become a thin session wrapper, nothing more.

---

## What Must Move

### From archie_loop.py routing → Archie system prompt

| Current location | Logic to move |
|---|---|
| `_generation_workflow_plan_for_message()` | BOM→diagram→WAF/Terraform sequencing rules |
| `_bom_diagram_pair_plan_for_message()` | BOM feeds diagram: run BOM first, pass payload to diagram |
| `_parallel_tools` block | POV+JEP can be requested in parallel; diagram+BOM cannot |
| `_tool_backed_action_reply()` | "If user requests download of existing artifact, return link" |
| `_is_change_update_intent()` | "If user says update all, identify impacted tools and re-run in order" |

### From archie_loop.py routing → stays as legitimate session management

| Block | Why it stays |
|---|---|
| Pending checkpoint approve/reject | Decision tracking state machine — not orchestration |
| Update confirm/cancel | Workflow state confirmation — not tool dispatch |
| History load/save | Session persistence |
| Context read/write | Customer state |

---

## Phased Approach

**p44a — Enrich Archie system prompt**
Add all sequencing rules from the routing blocks into `ORCHESTRATOR_SYSTEM_MSG`
in `archie_wiring.py`. Must merge before p44b. Low risk — additive only.

**p44b — Remove workflow_plan bypass**
Delete `workflow_plan` (lines 606–717) and `paired_bom_diagram_plan`
(lines 719–811) from archie_loop.py. These are the two largest bypass blocks
covering BOM, diagram, WAF, Terraform, POV, JEP requests. Requires p44a merged.

**p44c — Remove parallel_tools bypass**
Delete the `parallel_tools` block (lines 816–907). Forge's LLM decides
concurrency guided by the system prompt. Requires p44b merged.

**p44d — Architecture guard**
Add rule to CLAUDE.md. Add integration test that asserts `forge.run_turn()` is
called when a BOM message is sent. This test must fail if a bypass is
re-introduced.

---

## Success Criteria

1. A BOM request results in `forge.run_turn()` being called (verified by test)
2. Logs show `[FORGE] hat_auto_activated`, `expert_pre_action`, `expert_post_review` on every generation request
3. "Thinking..." events appear in the UI during generation
4. `archie_loop.py` is under 200 lines after migration
5. All existing non-live tests pass

---

## How To Prevent Regression

1. **CLAUDE.md rule** (added in p44d): "archie_loop.py is a session wrapper.
   It must not contain routing logic, LLM calls outside forge.run_turn(), or
   tool dispatch. Violations will break the Forge reasoning loop."

2. **Architecture test** (added in p44d): `tests/test_archie_forge_wiring.py`
   — mocks `forge.run_turn()` and asserts it is called when a BOM/diagram
   message is processed.

3. **Code naming**: After migration, rename `archie_loop.py` → `archie_session.py`
   so the filename signals its role.

4. **PR discipline**: Any PR that adds lines to `archie_loop.py` should
   trigger a question: "is this routing/orchestration logic? If yes, it belongs
   in Forge or the system prompt."

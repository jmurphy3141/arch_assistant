# Task: p58 scenario realism — provide POC logistics; accept grounded needs_input
Phase: 5
Status: todo

## Goal
The Northwind scenario asks for a POC plan and JEP but never provides POC
duration, owners, hours/week, success criteria, or in/out scope — so the poc/jep
producers CORRECTLY return `needs_input` (p58 re-run turns 9/14). That is grounding
working, not a product failure. A real SE supplies those before scoping a POC. Add
a natural turn that provides them so the POC/JEP become producible, and stop the
harness from scoring a legitimately-grounded `needs_input` as a failure.

Authorized by PLAN.md Decision #6 (understand before acting) — this fixes the test
scenario, not the product.

## Files to change
- `tasks/p58-native-engagement-sim.md` — add a Meeting-3 turn BEFORE the POC ask
  (current turn 9) that provides, in natural language: POC duration, Oracle +
  customer owners and hours/week each, success criteria, and explicit in/out scope.
  Renumber subsequent turns.
- `scripts/simulate_engagement_native.py` — insert the corresponding turn; and in
  scoring, distinguish "correctly asked for input the SE genuinely never supplied"
  (not a failure) from "failed to produce despite having the input" (a failure).

## Files to delete
- None.

## Do not touch
- All product code (`agent/**`, `sub_agents/**`, `skillforge/**`). Harness + task
  doc only.

## What to do
1. Add the POC-logistics turn to the scenario (task doc + harness), phrased as a
   real SE would after a discovery call.
2. Update scoring so a grounded `needs_input` for a genuinely-absent input is not a
   failure; a `needs_input` when the input WAS supplied still fails.

## Acceptance criteria
- With the logistics turn added, `generate_poc_plan` and `generate_jep` produce
  real artifacts (former turns 9/14) in the re-run. (recorded)
- A correct `needs_input` for a genuinely-absent input is not scored as a failure;
  a `needs_input` despite supplied input still fails. (assert in harness self-test)
- No product code changed.

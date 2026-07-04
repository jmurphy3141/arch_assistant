# Task: scenario tests draft-first POC/JEP (no required logistics)
Phase: 5
Status: done

## Goal
Correct the p58 scenario to match the real SE workflow: the SE does NOT have POC
duration, owners, or sign-offs when drafting, and sends a draft before they exist.
The earlier version of this task added a logistics turn — that was wrong. The
scenario must instead confirm the POC plan and JEP are produced as DRAFTS with
`[TBD]` placeholders for unknown logistics, never blocked by `needs_input`.

Depends on p69 (draft-first producers). Harness only — no product code.

## Files to change
- `tasks/p58-native-engagement-sim.md` — remove the requirement that the SE supply
  POC duration/owners/criteria before the POC/JEP turns. Keep the flow natural: the
  SE asks for a POC and later a JEP without having finalized logistics.
- `scripts/simulate_engagement_native.py` — revert the "supply logistics" turn;
  change the POC/JEP assertions to expect a produced DRAFT artifact whose unknown
  logistics render as `[TBD]` (assert the artifact exists and reloads, and that
  `[TBD]`/placeholder appears for absent duration/owners) — NOT `needs_input`.

## Files to delete
- None.

## Do not touch
- All product code (`agent/**`, `sub_agents/**`, `skillforge/**`). Harness + task
  doc only.

## What to do
1. Remove the injected logistics turn from the scenario (doc + harness).
2. Assert the POC/JEP turns produce a draft artifact with `[TBD]` placeholders for
   absent logistics; a `needs_input` for missing logistics now FAILS (it should
   draft instead).
3. Keep asserting no fabricated values (no invented duration/owner/number).

## Acceptance criteria
- Without any logistics provided, the POC and JEP turns produce reloadable draft
  artifacts containing `[TBD]` for unknown duration/owners/criteria. (assert)
- A `needs_input` for missing logistics is scored as a FAILURE (drafts are
  required). (assert in harness self-test)
- No fabricated logistics values appear. (assert)
- No product code changed.

# Task: draft-first POC/JEP producers (never require logistics)
Phase: 5
Status: todo

## Goal
The POC plan and JEP are DRAFTS an SE sends before logistics are finalized. They
must produce a complete-as-possible artifact from what is known, mark unknowns as
`[TBD]`, never fabricate a value, and never return `needs_input` for missing
logistics. p58 re-run turns 9/10/11/15 failed because the producers refused
(`needs_input`) for absent duration/owners/criteria/scope — which the SE does not
have at draft time.

Authorized by PLAN.md Decision #8 (never fabricate; producers ground to what is
supplied) — this corrects an over-strict "require everything" implementation.

## Files to change
- `agent/jep_composer.py` — validation is draft-first: REQUIRE only the core that
  makes it a real engagement doc (customer identity + workload). Render every other
  field (duration, phases, owners/commitments, success criteria, in/out scope,
  risks, approvals) from supplied facts where present, and as an explicit `[TBD]` /
  `[To be confirmed]` placeholder where absent. Do NOT invent values. Return an
  `ok` draft — not `needs_input` — whenever the core is present.
- `agent/poc_composer.py` — same draft-first rule for the POC brief/options: draft
  from known workload/pain/region; `[TBD]` for unknown duration/owners/criteria.
- `sub_agents/jep/server.py`, `sub_agents/poc_strategist/server.py` — the grounding
  gate: `input_grounding_missing` for these producers requires only customer +
  workload; missing logistics do NOT trigger `needs_input`. Keep the anti-
  fabrication self-review (no invented customer/number).

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- Other composers/sub-agents beyond poc/jep
- The excluded set
- Anti-fabrication behavior (still never invent a customer, number, or fact)

## What to do
1. Change poc/jep validation from "require all fields" to "require core (customer +
   workload); TBD the rest."
2. Render `[TBD]` placeholders for absent logistics; never a fabricated value.
3. Relax the poc/jep input-grounding gate to require only core grounding.
4. Keep output self-review anti-fabrication (customer named, no invented facts).

## Acceptance criteria
- With customer + workload but NO duration/owners/criteria, `generate_jep` and
  `generate_poc_plan` return `ok` with a real draft artifact whose logistics fields
  read `[TBD]` (not fabricated, not `needs_input`). (assert)
- With logistics supplied, those values appear instead of `[TBD]`. (assert)
- A draft never contains an invented customer, owner name, duration, or number not
  supplied. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + updated composer/grounding
  tests green.

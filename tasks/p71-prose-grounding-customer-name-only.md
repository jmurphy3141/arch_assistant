# Task: prose grounding = customer name, not fact-anchor substrings
Phase: 5
Status: done

## Goal
Stop the output grounding check from falsely rejecting valid prose artifacts. p58
re-run turn 8's POV was blocked with `needs_input: facts_not_reflected` even though
it named the customer and discussed the workload — because a valid POV paraphrases
facts ("Chicago region") rather than containing the exact stored value
("us-chicago-1") as a substring. The fact-anchor substring test is too brittle to
be a blocker; the customer name is the reliable grounding signal for prose.

Authorized by PLAN.md Decision #8 (producers ground to what's supplied) — this
corrects an over-strict output check (extends p65's structured relaxation to
prose).

## Files to change
- `sub_agents/grounding.py` — `output_grounding_missing`: remove the
  `facts_not_reflected` BLOCKER. For prose producers keep the customer-name check
  (`require_customer_name`); for structured keep the p65 behavior. The fact-anchor
  presence may remain in `trace` as an informational signal, but it must NOT
  produce `needs_input`. Input grounding and the anti-fabrication self-review are
  unchanged.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- The composers, the excluded set
- `input_grounding_missing` (producers still reject ungrounded INPUT)
- The customer-name requirement for pov/jep (still required in output)

## What to do
1. Drop `facts_not_reflected` as a `needs_input` cause; keep it (optional) in trace.
2. Keep customer-name enforcement for prose (pov/jep); keep structured behavior.
3. Keep input grounding + self-review anti-fabrication intact.

## Acceptance criteria
- A POV/JEP that names the customer but paraphrases facts (no exact fact substring)
  returns `ok`/grounded, not `needs_input`. (assert)
- A POV/JEP that omits the customer name still returns `needs_input`. (assert —
  turn-8 identity protection preserved)
- Ungrounded INPUT still returns `needs_input` for all producers. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + grounding tests green.

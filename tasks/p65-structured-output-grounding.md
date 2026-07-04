# Task: relax producer output-grounding for structured artifacts
Phase: 5
Status: done

## Goal
Stop p62's output-grounding from wrongly rejecting valid structured artifacts.
The p58 re-run's turn 11 diagram was blocked by `facts_not_reflected` because a
draw.io XML (or Terraform/BOM JSON) does not contain the engagement's fact values
as free-text substrings. The blunt substring check is fragile on serialized
artifacts. Keep the check where it belongs (prose artifacts that should name the
customer); relax it for structured outputs.

Authorized by PLAN.md Decision #8 (producers ground on produce) — this corrects
an over-strict implementation, not the intent.

## Files to change
- `sub_agents/grounding.py` — `output_grounding_missing`: add an
  `output_kind: "prose" | "structured"` parameter (default "prose"). For
  `structured`, verify INPUT grounding + identity (when required) but DO NOT run
  the `_fact_anchors` free-text substring test on the serialized artifact.
- `sub_agents/diagram/server.py`, `sub_agents/terraform/server.py`,
  `sub_agents/bom/server.py` — call `output_grounding_missing(..., output_kind="structured")`.
- `sub_agents/pov/server.py`, `sub_agents/jep/server.py` — keep prose behavior
  (still require the customer name in output). WAF stays prose.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- The composers, the excluded set
- Input grounding (`input_grounding_missing`) — unchanged; producers still reject
  ungrounded INPUT for all artifact types

## What to do
1. Add `output_kind` to `output_grounding_missing`; for `structured`, skip the
   fact-anchor substring test while keeping identity/input checks.
2. Mark diagram/terraform/bom producers as structured; keep pov/jep/waf as prose.
3. Producers still self-review and still report `trace.grounded`.

## Acceptance criteria
- A diagram whose draw.io XML reflects the topology but does not contain fact
  values as free text is `ok`/`grounded`, not `needs_input`. (assert in
  `tests/test_subagent_grounding.py`)
- A POV/JEP that omits the customer name still returns `needs_input`. (assert —
  the turn-8 protection is preserved)
- Ungrounded INPUT still returns `needs_input` for all producers. (assert)
- Forge mode unchanged → `pytest -m "not live"` green.
- Grounding tests green → `pytest tests/test_subagent_grounding.py -m "not live"`.

# Task: honest tool-result reporting (no fabricated success)
Phase: 5
Status: todo

## Goal
The native model must never claim an artifact was created or saved when the tool
returned `needs_input`/`error`/no artifact. p58 re-run turns 10 and 14: the tool
returned `needs_input`, yet the reply said "POC Plan v1 Ready (Saved:
poc/native-sim-.../v1.md)" / "JEP v1 Prep… teed up" — a fabricated artifact key and
success. This violates the identity's "never fabricate a stored fact" and is the
most serious residual: it makes failure paths untrustworthy.

Authorized by PLAN.md Decision #8 (grounding: never fabricate a deliverable or
stored fact).

## Files to change
- `agent/archie_wiring.py` — `NATIVE_SYSTEM_IDENTITY`: add a tool-result-honesty
  clause. Report a tool's actual status. If a tool returns `needs_input`, ask the
  user for exactly what it needs. NEVER state an artifact key, filename, "saved",
  or "ready" unless a tool returned that artifact key on this turn.
  (Coordinate with p64's generation-discipline clause — both edit this identity.)
- `agent/archie_native_loop.py` — ensure the `[TOOL RESULT]` appended for a
  non-`ok` result clearly carries `status` and the `clarification`, so the model
  cannot miss that no artifact was produced.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- Sub-agents, composers, the excluded set
- Loop control flow beyond the tool-result surfacing above

## What to do
1. Add the tool-result-honesty clause to the native identity.
2. Make the appended tool-result message unambiguous about status + missing input.
3. No routing rules — steer by identity, consistent with the native design.

## Acceptance criteria
- With a producer stubbed to return `needs_input`, the reply asks for the needed
  inputs and contains NO artifact key, filename, "saved", or "ready" claim.
  (assert in the native-loop test)
- With a producer returning an `artifact_key`, the reply may reference it. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + new tests green.

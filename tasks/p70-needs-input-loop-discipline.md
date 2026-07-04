# Task: needs_input loop discipline
Phase: 5
Status: done

## Goal
When a tool returns `needs_input`, the native model must surface the ask to the
user and stop — not retry the same producer with the same args to the tool limit.
p58 re-run turns 9/10/11 called `generate_poc_plan` 4–5 times and ended in
"I couldn't complete that request within the tool-call limit" (64–74s). Even after
p69 makes producers draft-first, this is the defensive backstop for any
`needs_input`.

Authorized by PLAN.md Decision #8 (report a tool's actual result) + Decision #6
(ask when input is genuinely needed).

## Files to change
- `agent/archie_native_loop.py` — add a minimal loop guard: if a tool returns
  `needs_input`, do not re-dispatch that same tool with the same args again this
  turn; surface the clarification to the user and end the turn cleanly. Also
  short-circuit an identical (tool, args) call that repeats — return the prior
  result instead of re-invoking. This is a deterministic anti-loop guard, not
  routing.
- `agent/archie_wiring.py` — one line in `NATIVE_SYSTEM_IDENTITY`: a `needs_input`
  result means ask the user for exactly what's missing and stop; do not re-call the
  same tool. (Coordinate with the p64/p67 identity clauses.)

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- Sub-agents, composers, the excluded set

## What to do
1. Track (tool, args) results within a turn; on a repeat identical call, return the
   cached result rather than re-invoking.
2. On `needs_input`, end the turn with the clarification surfaced to the user; do
   not keep iterating on that producer.
3. Add the matching identity line.

## Acceptance criteria
- A producer stubbed to return `needs_input` is invoked at most once per turn; the
  reply asks the user for the missing input and the turn ends without hitting the
  tool-call limit. (assert in the native-loop test)
- A normal successful tool sequence is unaffected. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + new tests green.

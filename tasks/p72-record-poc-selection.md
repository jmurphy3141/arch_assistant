# Task: record POC selection through the tool
Phase: 5
Status: todo

## Goal
When the user picks a POC option, the selection must be PERSISTED via
`generate_poc_plan` (confirm), not just acknowledged in prose. p58 re-run turn 10:
"Let's go with the second option" → the model replied "POC Decision Confirmed:
Option 2" but called NO tool, so the selection was never recorded and downstream
state has no selected option.

Authorized by PLAN.md Decision #8 (never claim a stored fact/decision without the
tool that records it).

## Files to change
- `agent/archie_wiring.py` — the `generate_poc_plan` tool description must make the
  confirm/selection path explicit: to record which POC option the user chose, call
  `generate_poc_plan` with the confirm action + the chosen option; a selection is
  not recorded until this tool call returns.
- `agent/archie_wiring.py` — one line in `NATIVE_SYSTEM_IDENTITY` (coordinate with
  the existing clauses): recording a decision (e.g. a chosen POC option) requires
  the tool call that persists it; do not merely narrate "confirmed."

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- The poc sub-agent's internal logic, composers, the excluded set
- Loop control flow

## What to do
1. Sharpen the `generate_poc_plan` tool description so the selection/confirm path is
   unambiguous.
2. Add the matching identity line: persist decisions via the tool, don't just
   narrate them.
3. No routing rules — steer via tool affordance + identity.

## Acceptance criteria
- Given prior POC options, "go with option 2" results in a `generate_poc_plan`
  confirm call and a recorded selected option in engagement state. (assert in the
  native-loop test with a stubbed poc tool)
- The reply does not claim a decision was recorded unless the tool call recorded
  it. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + new tests green.

## Note
This behavior is partly model-judgment (Grok 4 narrated a confirmation without
calling the tool). If it persists after the affordance/identity change, it is a
model-bound residual for the GPT-5 A/B, not a reason to add routing rules.

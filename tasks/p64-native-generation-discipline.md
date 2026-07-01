# Task: native generation discipline (converse/suggest, don't auto-generate)
Phase: 5
Status: todo

## Goal
Stop native mode over-generating artifacts on conversational turns (p58 re-run
turns 1 and 7 produced an STA / tech report unbidden). C3E awareness must GUIDE
and SUGGEST the next artifact, not auto-produce it. Generation happens only on an
explicit user request.

Authorized by PLAN.md Decision #6 ("Archie understands before acting") + Decision
#8 (C3E is standing context that guides).

## Files to change
- `agent/archie_wiring.py` — `NATIVE_SYSTEM_IDENTITY`. Add a clear generation-
  discipline clause: use the live C3E phase to *offer* the next artifact in one
  sentence, but call a `generate_*` tool ONLY when the user explicitly asks for
  that deliverable. A discovery/advice/intake turn is a conversation, not a cue to
  generate. Keep the identity tight — one added clause, not a ruleset.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- Sub-agents, composers, reference tools, the excluded set
- The native loop control flow (this is an identity/prompt change only)

## What to do
1. Extend `NATIVE_SYSTEM_IDENTITY` with the generation-discipline clause: converse
   and advise by default; surface the C3E next-required artifact as an offer; do
   not call a generate_* tool unless the user asked for that artifact this turn.
2. Do not add per-phrase routing rules or loop gates — this is model behavior
   steered by the identity, consistent with the native design.

## Acceptance criteria
- The identity contains a generation-discipline clause distinguishing "offer the
  next artifact" from "generate it," gated on explicit user request. (assert the
  clause is present in an updated native-loop test)
- Forge mode unchanged → `pytest -m "not live"` green.
- Live re-run signal (recorded, not asserted): p58 turns 1 and 7 no longer fire a
  generate_* tool on a conversational turn.

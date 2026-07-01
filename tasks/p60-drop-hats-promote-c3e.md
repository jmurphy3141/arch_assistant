# Task: drop hats, promote C3E to standing context
Phase: 5
Status: todo

## Goal
Remove the hat mechanism from the native path (per PLAN.md Decision #8) so the
manager delegates directly instead of "consulting a hat and stopping," and promote
C3E from a hat to Archie's standing identity + always-loaded engagement state.

Authorized by PLAN.md Decision #8 (supersedes Decision #2 in native mode).

## Files to change
- `agent/archie_native_loop.py` — stop registering `use_hat_*` tools; remove the
  hat-tool branch. The manager's tool list is sub-agents + lookups + memory +
  reference tools only. No hat schemas.
- `agent/hat_engine.py` — remove the native hat-tool exposure added in p57
  (`get_native_hat_tool_schemas`, `invoke_native_hat`). Leave the forge-path hat
  functions untouched.
- `agent/archie_wiring.py` — fold the C3E **methodology** (phase order Qualify →
  Discover → Develop → Design → Prove → Win → Deploy → Support → Grow, and the
  gating artifacts per phase) into the native system identity as standing context.
  Keep it concise; it is who Archie is, not a lens.
- `agent/archie_native_loop.py` — ensure the working set (from p59) always includes
  the live C3E **phase state** from `engagement_mission.py` (current phase,
  blockers, next required artifact), so Archie is C3E-aware every turn.

## Files to delete
- None. Forge-path hats and `agent/hats/*.md` stay for forge mode; the domain
  hats' factual reference is relocated by p61, not deleted here.

## Do not touch
- `skillforge/forge.py` and the forge path (hats remain fully functional there)
- `sub_agents/**` internals and composers
- The object-storage layout and the Forge `excluded` set

## What to do
1. Remove hat tools from the native path: no `use_hat_*` in the native tool list,
   no hat-tool dispatch branch. `grep` must show no hat-tool registration in
   `agent/archie_native_loop.py`.
2. Add the C3E methodology to the native system identity (standing context, not a
   tool). Keep prose tight.
3. Make the p59 working set always carry the C3E phase state from
   `engagement_mission.py` so it is present every turn without the model asking.
4. Forge mode is unchanged — hats still load and inject exactly as before.

## Acceptance criteria
- `agent_mode: native`: the model's tool list contains NO `use_hat_*`; a BOM/POC
  request delegates straight to the sub-agent (no hat round in the trace). (assert
  in an updated `tests/test_archie_native_loop.py`)
- The native system identity contains the C3E phase order and gating artifacts;
  the assembled working set includes current phase + next required artifact.
- `agent_mode: forge`: hats behave exactly as before → `pytest -m "not live"` green.
- Re-run p58 (live) after this lands: turns 9 and 12 (the hat-trap) delegate to
  the sub-agent instead of stopping at a hat.

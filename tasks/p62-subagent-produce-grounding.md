# Task: sub-agent produce-grounding + self-review
Phase: 5
Status: done

## Goal
Make every sub-agent ground its output to the engagement (identity + facts) and
self-review before returning, so producers never emit an ungrounded artifact.
Fixes p58 turn 8 (POV persisted but did not name Northwind Health).

Authorized by PLAN.md Decision #8 ("sub-agents are grounding producers, not
document factories").

## Files to change
- `agent/sub_agent_client.py` — when the manager delegates, ALWAYS include the
  engagement identity (customer name/id) and the current fact set in the A2A
  `engagement_context`, per each agent card's declared inputs. The manager must
  hand the producer what it needs to ground.
- `sub_agents/pov/server.py`, `sub_agents/jep/server.py`,
  `sub_agents/bom/server.py`, `sub_agents/diagram/server.py`,
  `sub_agents/waf/server.py`, `sub_agents/terraform/server.py` — each producer:
  1. must incorporate the customer identity + engagement facts it was given
     (a POV/JEP names the customer; a BOM/diagram reflects the stated scope);
  2. runs a deterministic self-review before returning and reports it in `trace`
     (e.g. `grounded: true/false`, `missing: [...]`). If a required grounding
     field is absent, return `status: needs_input` naming it — do not emit an
     ungrounded artifact.
- Each sub-agent's `system_prompt.md` — one line: ground to the provided customer
  and facts; never invent a customer, number, or fact not supplied.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- The composers' core generation logic (`jep_composer`, `poc_composer`, etc.) —
  add the grounding/self-review check around them, do not rewrite them
- The Forge `excluded` set

## What to do
1. In `sub_agent_client.py`, populate `engagement_context` with customer identity +
   facts for every delegated call (only the fields each card declares).
2. In each sub-agent, use that identity/facts in the output and add a deterministic
   self-review: verify the customer is named and required facts are reflected;
   set `trace.grounded` and `trace.missing`; downgrade to `needs_input` when a
   required grounding field is missing rather than shipping ungrounded.
3. Keep it deterministic — no extra LLM review pass required.

## Acceptance criteria
- Given an engagement with customer "Northwind Health", a generated POV/JEP names
  the customer and `trace.grounded` is true. (assert in
  `tests/test_subagent_grounding.py`)
- Given a delegation missing a required grounding field, the sub-agent returns
  `status: needs_input` naming the field and produces NO artifact. (assert)
- `sub_agent_client` includes engagement identity + facts in `engagement_context`
  for each producer call. (assert)
- Forge mode unchanged → `pytest -m "not live"` green.
- New tests green → `pytest tests/test_subagent_grounding.py -m "not live"`.

# Task: last-resort deterministic grounding guard (conditional)
Phase: 5
Status: todo — DO NOT START until after p60–p62 land AND a model A/B (Grok 4 vs
GPT-5.x) still shows persistent fabrication of specific deal claims.

## Goal
A single deterministic safety net: flag (and optionally strip) a specific
*quantified claim about this engagement* in a conversational reply that is not
traceable to the engagement's data — WITHOUT blocking legitimate hedged advisory
figures. This is the one guard PLAN.md Decision #8 permits; it is a net, not
routing. Build only if the model can't hold grounding after p60–p62.

Authorized by PLAN.md Decision #8 (optional deterministic guard) + the existing
`safety_rules.py` guard slot (≤100 lines, no LLM).

## Files to create
- `agent/grounding_guard.py` — deterministic reply check. Distinguish:
  - **Allowed (hedged advisory):** figures with hedging/generic framing
    ("typically", "often ~30–40%", "roughly", "in general") and no claim they are
    this engagement's numbers.
  - **Flagged (claimed fact):** a specific number/price/% asserted as this deal's
    value ("Northwind will save 40%", "$X/mo for you") with no matching value in
    the engagement facts / stored artifacts.
  Also flag fabricated attributed evidence (named customer + result/SLA not in
  context — the "ETC 300% / Zimperium" class).
- `tests/test_grounding_guard.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — run the guard on the final reply; when it flags a
  claimed fact, either strip/soften it or append a "not from engagement data" note
  per config; record the flag in the turn trace. Never touch tool outputs/artifacts.
- `config.yaml` — `orchestrator.grounding_guard: off` (default). Enable explicitly.
- `scripts/simulate_engagement_native.py` — refine the p58 grounding assertion to
  the SAME hedged-vs-claimed distinction, so advisory ballparks (e.g. turn 5's
  hedged 40%) PASS and only claimed-fact fabrications FAIL.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- `sub_agents/**`, composers, artifacts (the guard only inspects the chat reply)
- The Forge `excluded` set

## What to do
1. Build the deterministic hedged-vs-claimed classifier (no LLM). Default `off`.
2. Wire it into the native reply path behind the flag; record flags in trace.
3. Update the p58 harness grounding assertion to match, so the re-run judges
   advisory ballparks and claimed-fact fabrications correctly.

## Acceptance criteria
- "customers often see ~30–40% savings" → NOT flagged. (assert)
- "Northwind will save 40%, about $6k/mo" with no such figure in the facts →
  flagged as claimed-fact. (assert)
- "ETC saw a 300% improvement" (named source not in context) → flagged as
  fabricated evidence. (assert)
- Guard `off` by default → native replies pass through untouched; forge unchanged.
- New tests green → `pytest tests/test_grounding_guard.py -m "not live"`;
  `pytest -m "not live"` green.

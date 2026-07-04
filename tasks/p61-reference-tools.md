# Task: reference/lookup tools for hard facts
Phase: 5
Status: done

## Goal
Give the native manager reference/lookup tools for the specific facts the domain
hats used to carry (SKU families, pricing, HA multipliers, reference
architectures), so conversational answers about specifics are grounded by
retrieval instead of fabricated. Fixes p58 turn 7 (invented costs/sizes).

Authorized by PLAN.md Decision #8 ("specific/proprietary reference → reference
tools the manager consults").

## Files to create
- `agent/reference_tools.py` — thin, deterministic lookup handlers over EXISTING
  data sources (do not restate facts in a prompt):
  - `lookup_compute_shapes()` — E5/E6/A1/X9 families + OCPU/mem ranges from the
    shape catalog already in `bom_service`/`oci_standards`; state silicon truthfully
    (E5/E6 = AMD x86, A1 = Ampere/Arm).
  - `lookup_price(sku_or_service)` — current unit price from the live pricing cache
    in `bom_service`; return "unpriced/TBD" when absent, never a guess.
  - `lookup_reference_architecture(query)` — match against `reference_architecture.py`
    / `agent/standards/oracle_reference_bundle.json`.
- `tests/test_reference_tools.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — register the three reference tools in the native
  tool list so the model can consult them mid-conversation.
- `agent/archie_wiring.py` — a one-line note in the native identity that specifics
  (shapes, prices, reference patterns) come from these tools, not memory.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path
- `sub_agents/**` internals and composers
- `bom_service` pricing logic, `oci_standards.py`, `reference_architecture.py`
  internals — READ from them, do not modify them
- The Forge `excluded` set

## What to do
1. Build `reference_tools.py` as thin read-only wrappers over the existing pricing
   cache, shape catalog, and reference bundle. No new facts are authored; no LLM
   calls; unknowns return "unknown/TBD", never a fabricated value.
2. Register the tools in the native loop only.
3. Keep the handlers deterministic and fast.

## Acceptance criteria
- `lookup_price` returns the cached price for a known SKU and "unpriced/TBD" for an
  unknown one (never a nonzero guess). (assert)
- `lookup_compute_shapes` reports E5/E6 as AMD x86 and A1 as Ampere/Arm. (assert)
- `lookup_reference_architecture` returns a match from the existing bundle for a
  known pattern and empty for an unknown one. (assert)
- `agent_mode: native`: the three tools appear in the model's tool list.
- `agent_mode: forge` unchanged → `pytest -m "not live"` green.
- New tests green → `pytest tests/test_reference_tools.py -m "not live"`.

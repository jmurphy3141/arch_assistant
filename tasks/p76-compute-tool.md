# Task: deterministic compute tool (native)
Phase: 5
Status: done

## Goal
Give the native model a deterministic calculator so cost/TCO/proration/sizing math
is computed, never done in the model's head. An LLM doing "16 OCPU × $0.03 × 730 ×
36" mentally is the exact fabrication risk we've been closing — this makes the
number exact and grounded.

Authorized by PLAN.md Decision #8 (grounding; never fabricate a figure).

## Files to create
- `agent/compute_tools.py` — a `compute` handler + native-only
  `get_compute_tool_specs()` (mirror `agent/reference_tools.py`; NOT registered via
  `forge.register_tool`). It takes a `{ "expression": "<arithmetic>" }` and returns
  the exact result.
  - **Safe evaluation only — no `eval`/`exec`.** Parse with `ast.parse(..., mode="eval")`
    and whitelist nodes: numeric constants, `+ - * / ** %`, unary minus, and
    parentheses. Reject names, calls, attributes, subscripts, comprehensions — any
    non-arithmetic node → `status="error"` with a clear message. Use `Decimal` for
    money-grade precision; return both the numeric result and the normalized
    expression in `data`.
- `tests/test_compute_tools.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — `registered_specs.extend(compute_tools.get_compute_tool_specs())`
  (native path only).
- `agent/archie_wiring.py` — one line in `NATIVE_SYSTEM_IDENTITY`: for any numeric
  calculation (totals, TCO, proration, percentages), use the compute tool — never
  do the arithmetic yourself. (Coordinate with the existing numbered rules.)

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path; do NOT register via `forge.register_tool`
- The excluded set, sub-agents, composers

## Tool description
"Compute exact arithmetic for cost, TCO, proration, sizing, and percentage math
(for example 16*0.03*730 for monthly compute, 14/30.4 for POC proration, a 36-month
projection, or a percentage). Use whenever an answer requires a calculation. Do not
perform the arithmetic yourself."

## Acceptance criteria
- `16*0.03*730` → 350.4; `(2+3)*4` → 20; `14/30.4` ≈ 0.4605 (Decimal precision). (assert)
- A non-arithmetic input (`__import__("os")`, a bare name, a function call) returns
  `status="error"`, never evaluates, never raises. (assert)
- `compute` appears in the native tool list; NOT on the forge path →
  `pytest -m "not live"` green, forge unchanged. (assert)
- New tests green → `pytest tests/test_compute_tools.py -m "not live"`.

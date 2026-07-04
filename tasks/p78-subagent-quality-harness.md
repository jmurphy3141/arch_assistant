# Task: sub-agent quality-eval harness
Phase: 6
Status: done

## Goal
Measure sub-agent artifact quality so it can be improved against a baseline (per
PLAN.md Decision #9). Today p58 only checks that the right sub-agent fired and
produced a reloadable file — never whether the artifact is any good. This builds
the instrument: deterministic checks + a rubric LLM-judge distribution + human
calibration, producing a per-artifact, per-dimension baseline. Measurement only —
no sub-agent behavior changes.

Authorized by PLAN.md Decision #9 + Phase 6.

## Files to create
- `scripts/eval_subagent_quality.py` — the harness:
  - For a fixed engagement fixture, generate each artifact type (pov, jep, bom,
    diagram, waf, terraform) N times via the existing A2A sub-agents.
  - **Objective layer (deterministic):** per type — structural completeness
    (jep sections/phases/owners; bom priced line items that sum; diagram tiers;
    pov names the customer), grounding fidelity (numbers/services trace to the
    fixture inputs), format/correctness (docx/xlsx/drawio parse; run
    `terraform validate` on the terraform bundle; bom monthly math checks).
  - **Subjective layer (LLM-judge):** load the per-type rubric; ask a JUDGE model
    (configurable `model_id`, distinct from the producer) to score each anchored
    1–5 dimension, M times per artifact; when a golden exemplar exists, judge
    PAIRWISE ("is this better than the exemplar, and on which dimensions?").
  - Aggregate per-artifact, per-dimension score distributions + objective pass
    rates; write to `docs/subagent-quality.json`.
  - `--pairwise A.json B.json` mode: judge two artifact sets head-to-head, report
    win rate per dimension (the A/B primitive for later tuning tasks).
  - `--calibrate <se_scores.json>`: ingest SE-labelled scores and report the
    judge-vs-SE correlation per dimension (the trust anchor).
- `eval/rubrics/{pov,jep,bom,diagram,waf,terraform}.md` — per-type rubrics, each
  dimension with explicit 1–5 anchors ("5 = …", "1 = …").
- `eval/golden/` — directory for SE-endorsed exemplars (may start empty; the
  harness runs without them and simply skips pairwise-vs-golden).
- `tests/test_eval_subagent_quality.py` — harness self-tests (mocked judge + a
  fixture artifact), not live.

## Files to change
- None in product code.

## Do not touch
- The sub-agents, composers, `skillforge/forge.py`, the excluded set — this task
  MEASURES; it changes no producer behavior.

## What to do
1. Build the fixture engagement + the objective checks per artifact type.
2. Write the six anchored rubrics.
3. Build the judge pass (N runs, distribution), pairwise mode, and calibration mode.
4. Aggregate to docs/subagent-quality.json; keep raw per-run judgments.

## Acceptance criteria
- Running the harness on the fixture produces, per artifact type: objective
  pass/fail per check AND a per-dimension score distribution (min/median/max across
  M judge runs). (assert on a mocked judge)
- `--pairwise` returns a per-dimension win rate between two artifact sets. (assert)
- `--calibrate` reports judge-vs-SE correlation on a labelled set. (assert)
- No product/sub-agent code changed → `pytest -m "not live"` green.
- Harness self-tests green → `pytest tests/test_eval_subagent_quality.py -m "not live"`.

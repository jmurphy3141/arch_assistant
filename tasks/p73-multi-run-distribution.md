# Task: multi-run p58 — pass-rate distribution + variance
Phase: 5
Status: todo

## Goal
Single p58 runs are noisy (11/16, 12/15, 9/15 on identical code) because the model
is non-deterministic. Replace the single-sample verdict with a DISTRIBUTION: run
the scenario N times and report per-turn pass RATE and overall variance. This both
(a) separates consistently-broken turns (worth fixing) from flaky ones (model
noise, wait for a stronger model), and (b) is the instrument for the Grok-4 vs
GPT-5.x A/B — run each model N times, compare distributions.

Harness only. No product code.

## Files to change
- `scripts/simulate_engagement_native.py` — add `--runs N` (default 1). When N>1,
  run the full scenario N times against the same isolated stack (fresh engagement
  id per run), aggregate, and write to the evidence JSON:
  - per-turn pass rate (e.g. `T7: 0/5`, `T9: 3/5`), plus which failure strings
    recurred and how often;
  - overall pass distribution (min/median/max PASS count across runs);
  - latency stats (median, p95, max) across all turns/runs;
  - the raw per-run results retained.
  Keep single-run behavior identical when `--runs 1`.

## Files to delete
- None.

## Do not touch
- All product code (`agent/**`, `sub_agents/**`, `skillforge/**`).

## What to do
1. Add `--runs N`; loop the existing scenario N times with a fresh engagement id
   each run; aggregate per-turn pass rate + variance + latency stats.
2. Preserve raw per-run evidence in the JSON.
3. Add a short summary block: per-turn pass rate sorted worst-first.

## Acceptance criteria
- `--runs 5` produces an aggregate report with per-turn pass rates, overall
  min/median/max PASS, and latency stats; raw runs retained. (assert on a mocked
  multi-run)
- `--runs 1` behaves exactly as today.
- No product code changed.

## Use
- Baseline now: `--runs 5` on Grok 4 → the true pass-rate distribution.
- A/B in ~2 weeks: `--runs 5` on GPT-5.x, identical architecture → compare
  distributions. Turns that are 0/5 on both are architecture/model-hard; turns
  that jump from flaky-on-Grok to solid-on-GPT-5 were the consistency residual.

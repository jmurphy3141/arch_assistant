# Task: harness grounding-assertion accuracy
Phase: 5
Status: done

## Goal
Fix the p58 harness so its grounding assertion stops producing false positives.
Fabrication has cleared in native mode; the remaining "fabrication" FAILs are the
harness flagging REAL numbers. Make the score trustworthy so we can read the true
model-bound residual. (Supersedes the earlier conditional-guard scope — the
deterministic reply guard is not needed while the model isn't fabricating.)

Authorized by PLAN.md Decision #8 (grounding definition: allow hedged advisory
figures; flag only claims not traceable to engagement data OR artifacts).

## Files to change
- `scripts/simulate_engagement_native.py` — `_fabrication_errors()` (~line 425).
  Three fixes:
  1. **Unit normalization** before matching: treat ms/milliseconds, gb/GB,
     tb/TB, users/concurrent users, rps/requests per second as equivalent, so
     "600ms" matches notes "600 milliseconds".
  2. **Include produced artifacts in the grounded corpus**: fold the text/values
     of this engagement's stored artifacts (BOM subtotal/line items, diagram
     labels, etc.) into `grounded_text`, so a reply figure that matches a real
     artifact (e.g. "$368" ≈ BOM $367.64, within a small tolerance) is grounded.
  3. **Allow hedged advisory framing**: a figure carrying hedging within the same
     clause ("typically", "often", "roughly", "~", "up to", "in general") and not
     asserted as this engagement's measured value is NOT flagged.
  Keep flagging: a specific number asserted as this deal's value with no match in
  notes/facts/artifacts, and fabricated attributed evidence (named source +
  achieved/reported/saved).

## Files to delete
- None. (The `agent/grounding_guard.py` module from the prior p63 scope is not
  built; fabrication is handled by producer grounding, not a reply guard.)

## Do not touch
- The native loop, sub-agents, composers, forge path — this is harness-only.

## What to do
1. Add a unit-normalizer and apply it to both the reply value and `grounded_text`
   before the membership test.
2. Build the artifact corpus for the engagement (reload stored BOM/diagram text)
   and append to `grounded_text`; match numbers with a small relative tolerance.
3. Add the hedged-advisory exemption.
4. Re-score the existing evidence: turns whose only failure was a real,
   note-/artifact-backed figure (5, 15, and the sizing-number noise on 7) must no
   longer be flagged.

## Acceptance criteria
- "600ms" with notes "600 milliseconds" → NOT flagged. (assert)
- A reply figure equal (within tolerance) to a produced BOM subtotal → NOT
  flagged. (assert)
- "customers often see ~30–40% savings" → NOT flagged. (assert)
- "Northwind will save 40% = $6k/mo" with no such value anywhere → flagged. (assert)
- Harness self-tests green; no product code changed.

# Task: native tool-surface clarity (disambiguate lookups, fix generate triggers, resolve identity conflict)
Phase: 5
Status: done

## Goal
Fix the prompt/tool-surface causes of the residual native failures. The Grok-4 →
Grok-4.3 A/B proved the split: the model fixed the consistency/generation turns
(T7 0→4, T11 2→5, T12 3→5) but the LOOKUP band did NOT improve (T3 2→1, T13 1→1,
T15 3→2). A better model can't fix lookups because they're **tool-surface
ambiguity** — six overlapping "retrieve" tools with no boundaries. This is the
controllable lever.

Authorized by PLAN.md Decision #8 (grounding by tool use; C3E is standing context
that guides, not a generation trigger).

## Files to change
- `agent/archie_wiring.py`
  1. **Resolve the identity conflict.** `NATIVE_SYSTEM_IDENTITY` currently pairs
     "call a generate_* tool only when the user explicitly asks" with a C3E
     phase→gate-artifact mapping ("Discover: Strategic Technical Approach…") plus
     "use the live C3E phase to guide the engagement" — which reads as "produce
     the phase's artifact." Rewrite so C3E guides ONLY what to *offer in one
     sentence*, never what to generate; make the explicit-request rule
     unambiguous and dominant. Tighten the run-on paragraph into distinct rules.
  2. **`generate_tech_report`** — remove the conversational trigger. Its current
     description says "Call when the user asks … how to architect a workload,"
     which matches conversational architecture questions (T7). Gate it to an
     explicit request for a *written research/options-comparison report*; do NOT
     fire on opinions/advice ("gut on the architecture"). Consider whether it
     belongs in the native surface at all.
  3. **De-dupe generate triggers.** `generate_presentation` and
     `generate_sales_deck` both trigger on "deck/presentation/slides"; `generate_pov`
     / `generate_sales_deck` / `generate_technical_proposal` overlap on
     "brief/briefing/proposal." Give each a single, non-overlapping trigger.
- `agent/archie_memory_retrieval.py` (and wherever the retrieve tools are
  described) — **disambiguate the six retrieve tools** so each has a distinct
  "use this when … NOT the others" boundary:
  - `list_artifacts` — list every produced deliverable + links ("what have we
    produced / do we have anything"). NOT for reading one artifact or facts.
  - `get_document(type)` — fetch & read ONE deliverable of a type to answer
    whether it exists / what it says ("did the BOM include X", "show the JEP").
  - `get_summary` — the engagement's gathered FACTS/summary. NOT artifacts.
  - `search_notes` — keyword search of uploaded NOTES. NOT artifacts.
  - `recall_fact(query)` — the current value of ONE specific fact. NOT a document.
  - `get_meeting_summaries` — per-meeting session summaries.
- (Optional) trim rarely-used `generate_*` tools from the native tool list to
  reduce selection noise.

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path (its system prompt/sequencing rules)
- Sub-agents, composers, the excluded set

## What to do
1. Rewrite `NATIVE_SYSTEM_IDENTITY`: distinct rules; C3E guides conversation only;
   explicit-request generation rule dominant and unambiguous.
2. Fix `generate_tech_report` trigger (no conversational architecture questions).
3. Give the six retrieve tools mutually-exclusive "use when / not when" descriptions.
4. De-dupe deck/proposal/pov triggers.

## Acceptance criteria
- Native identity no longer instructs generation from the C3E phase; the
  explicit-request rule is singular and unambiguous. (assert the identity text)
- Each retrieve tool's description names when to use it AND at least one sibling it
  is NOT for. (assert)
- `generate_tech_report` description no longer contains a conversational trigger
  ("how to architect a workload"). (assert)
- Forge mode unchanged → `pytest -m "not live"` green + updated tests.
- Live signal (recorded via p73 --runs 5 on Grok 4.3): lookup band (T3/T13/T15)
  climbs; T7 reaches 5/5; no regression on the 5/5 turns.

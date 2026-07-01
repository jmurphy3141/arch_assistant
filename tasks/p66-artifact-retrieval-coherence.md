# Task: artifact retrieval coherence
Phase: 5
Status: todo

## Goal
`get_document` / `list_artifacts` must find artifacts that the `generate_*`
producers just persisted in native mode. p58 re-run turn 12 produced a BOM (PASS),
but turn 13's `get_document("bom")` returned "No bom document found" five times and
the model looped to the tool limit. The lookup and the generator disagree on where
an engagement's artifacts live.

Authorized by PLAN.md Decision #8 (lookups fetch what exists) + Memory
Requirements (engagement artifact index).

## Files to change
- `agent/archie_native_loop.py` — on a successful producer result that carries an
  `artifact_key`, register it in the engagement's retrievable artifact index
  (type → {key, summary, download}) — the SAME index `get_document`/`list_artifacts`
  read.
- `agent/tools/notes.py` — `get_document(type)` and `list_artifacts` read from that
  index so a just-produced artifact is found on the next turn.
- `agent/document_store.py` (or `context_store.py`) — add
  `register_artifact(...)` / `get_latest_artifact_by_type(...)` if not present, so
  there is ONE per-engagement source of truth for "what artifacts exist."

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path (its own persistence)
- Sub-agents, composers, the excluded set

## What to do
1. Define/confirm one engagement artifact index keyed by type.
2. Write to it from the native loop whenever a producer returns an artifact_key.
3. Read from it in `get_document`/`list_artifacts`.
4. Keep forge's persistence path unchanged.

## Acceptance criteria
- After `generate_bom` succeeds, `get_document("bom")` returns the BOM (key +
  summary) in ONE call, and `list_artifacts` includes it. (assert)
- "did the BOM include X" resolves via a single `get_document` — no repeated
  not-found loop. (recorded in the re-run; asserted at unit level)
- Forge mode unchanged → `pytest -m "not live"` green + new tests green.

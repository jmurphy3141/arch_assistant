# Task: per-client transcript memory (distill + cite + semantic retrieval)
Phase: 7
Status: todo — sequenced AFTER Phase 6 (sub-agent quality)

## Goal
Now that all meetings are recorded and transcribed, give each engagement deep,
retrievable memory of what was actually said — WITHOUT drowning the model in raw
transcript noise. Per Decision #10: distill-don't-dump, cite, and keep per-client
memory strictly isolated. This is the memory enabler under Phase 7 learning.

## Files to create
- `agent/transcript_ingest.py` — ingest an uploaded transcript for an engagement:
  1. **Distill:** run the existing debrief/relationship extraction
     (`archie_memory` / debrief loop) over the transcript → facts, decisions,
     objections, commitments, action items, and a concise meeting summary. Route
     through the SAME human-confirm (`confirm_debrief`) path — nothing persists to
     engagement memory until confirmed. Each extracted item carries a transcript
     citation (meeting id + line/offset); ASR-uncertain numbers/names are flagged
     `low_confidence` for confirmation, never asserted.
  2. **Index (raw, for retrieval/citation only):** chunk the raw transcript and
     embed it into a PER-CLIENT semantic index (isolated namespace per
     customer/engagement). Use an OCI GenAI embedding model; the vector store is the
     implementer's choice but MUST enforce per-client isolation.
- `agent/semantic_notes.py` (or extend the p59 memory retrieval) — a native-only
  `semantic_search` over the active engagement's notes + transcript index that
  returns cited passages ("per the <date> call: …"). Register via the native loop's
  `registered_specs.extend(...)`; give it a description that disambiguates from the
  keyword `search_notes` (semantic = meaning/paraphrase; keyword = exact term).
- `tests/test_transcript_memory.py`.

## Files to change
- `agent/archie_native_loop.py` — register `semantic_search` (native only).
- `server/routes/documents.py` (or the notes-upload path) — when an uploaded file
  is a transcript, route it through `transcript_ingest` (distill + index) instead
  of treating it as a generic note.

## Do not touch
- `skillforge/forge.py` and the forge path
- Artifact producers/composers — they receive DISTILLED, confirmed facts only,
  never raw transcript
- The cross-client knowledge corpus (a separate Phase 7 task)
- The Forge `excluded` set

## Hard rules (Decision #10)
- Raw transcript is stored + indexed for RETRIEVAL/CITATION only; it never feeds
  artifact production or is stuffed into context wholesale.
- Distilled facts persist only after human confirm; each carries a citation.
- Per-client isolation is absolute — engagement A's index is never queried for B.

## Acceptance criteria
- Ingesting a transcript produces distilled facts/objections/commitments + a
  summary, each with a transcript citation; nothing persists until confirmed. (assert)
- The raw transcript is chunked + embedded into the engagement's isolated index.
- `semantic_search` returns the correct passage for a PARAPHRASED query that keyword
  `search_notes` misses (fixture: transcript says "regulatory audits", query asks
  about "compliance"). (assert)
- Engagement isolation: a query under engagement A never returns B's passages. (assert)
- Producers are never handed raw transcript — only distilled facts. (assert)
- Forge mode unchanged → `pytest -m "not live"` green + new tests green.

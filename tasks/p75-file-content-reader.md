# Task: general file-content reader tool (native)
Phase: 5
Status: done

## Goal
Give the native model a general tool to READ the actual contents of a stored
spreadsheet or document so it can answer questions about what's inside — closing
T13 ("did the BOM include Gen AI tokens?") and, more importantly, letting Archie
review ANY spreadsheet/doc an SE deals with (customer cost sheets, sizing
worksheets, uploaded xlsx), not just BOMs.

Root cause of T13: `get_document` returns only a summary + download link for a
produced BOM, never the rows — so the model cannot see the line items and either
guesses or loops. `get_document` stays as the existence/inventory tool; this new
tool is the deep-read companion.

Authorized by PLAN.md Decision #8 (grounding by tool use; the model reads real
content instead of guessing).

## Files to create
- `agent/file_reader_tools.py` — a `read_file_content` handler + native-only
  `get_file_reader_tool_specs()` (mirror `agent/reference_tools.py`; NOT registered
  via `forge.register_tool`, so forge mode is untouched):
  - Resolve a stored file by: produced-artifact type (bom/pov/jep/diagram/…) via
    `context_store.get_latest_artifact_by_type`, OR an uploaded file by name/key via
    `document_store`.
  - Fetch the bytes from the store; detect type; parse to readable text:
    - `.xlsx` → `openpyxl` (data_only, read_only): each sheet's non-empty rows as
      `col | col | col` lines, prefixed by the sheet name (reuse `bom_parser`'s
      openpyxl usage — do not re-invent).
    - `.pdf/.docx/.txt/.md/.csv` → reuse `agent/note_extractor.extract_text` (add
      `.xlsx` there only if it doesn't already handle it, or handle xlsx in this
      module).
  - Return the content in `data={"content": …}`, truncated to a sane budget
    (~12000 chars) with a truncation note, like `get_document_section`.
  - Missing/unresolvable file → `needs_input` naming what to provide.
- `tests/test_file_reader_tools.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — `registered_specs.extend(file_reader_tools.get_file_reader_tool_specs())`
  (native path only, same place reference tools are added).
- `agent/archie_wiring.py` — one line in `NATIVE_SYSTEM_IDENTITY`: to answer a
  question about what a file or spreadsheet contains, read its contents with
  `read_file_content` — never answer from a summary. (Coordinate with the existing
  numbered rules.)

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path; do NOT register this via
  `forge.register_tool` (keep it native-only)
- The Forge `excluded` set; `get_document` behavior; sub-agents; composers

## Tool description (must disambiguate vs get_document)
"Read and return the full contents of a stored spreadsheet or document — its rows,
values, and sections — to answer detailed questions about what is inside (for
example 'did the BOM include Gen AI tokens?', 'what is the total in this sheet?',
'review this uploaded worksheet for X'). Use whenever the answer requires looking
at what a file actually contains. Different from get_document, which only reports
whether a deliverable exists and gives its link."

## Acceptance criteria
- `read_file_content` on a stored BOM `.xlsx` returns the line-item rows as text
  (SKU / description / quantity visible), enabling a token/line-item scan. (assert
  with a fixture workbook)
- On a `.pdf`/`.docx`/`.txt` it returns extracted text; on a missing file it
  returns `needs_input`. (assert)
- The native tool list includes `read_file_content`; it is NOT registered on the
  forge path → `pytest -m "not live"` green, forge unchanged. (assert)
- New tests green → `pytest tests/test_file_reader_tools.py -m "not live"`.
- Live signal (p73 --runs 5 on Grok 4.3): T13 climbs — the model calls
  `read_file_content`, answers the Gen AI-token question from real rows, no
  fabrication, no loop.

# Task: artifact export/convert tool (native)
Phase: 5
Status: todo

## Goal
Let the native model export/convert a produced artifact to a shareable format —
the diagram to PNG, a spreadsheet to CSV — and hand back a download link. This is
how an SE actually delivers deliverables ("give me a PNG of the diagram", "a CSV of
the BOM").

Authorized by PLAN.md Decision #8 (grounding by tool use; deterministic conversion
of an already-produced artifact).

## Files to create
- `agent/export_tools.py` — an `export_artifact` handler + native-only
  `get_export_tool_specs()` (mirror `agent/reference_tools.py`; NOT via
  `forge.register_tool`). Args: `{ "type": "<artifact type>", "format": "png|csv" }`.
  - Resolve the latest produced artifact of `type` (via
    `context_store.get_latest_artifact_by_type` / the store).
  - **drawio → png:** reuse the existing PNG export path
    (`agent/png_exporter.py` / any existing `agent/tools/export.py`). If the drawio
    CLI is unavailable, return `status="error"` with "PNG export CLI unavailable" —
    do NOT crash.
  - **xlsx → csv:** open with `openpyxl` (read_only) and write CSV; always available.
  - Store the converted bytes at a derived key alongside the source; return
    `status="ok"`, the new `artifact_key`, and a download link in `data`.
  - Unknown type/format or missing source → `needs_input`/`error` (named), never a crash.
- `tests/test_export_tools.py` — the acceptance tests below.

## Files to change
- `agent/archie_native_loop.py` — `registered_specs.extend(export_tools.get_export_tool_specs())`
  (native path only).
- `agent/archie_wiring.py` — no identity change required (the tool description carries it).

## Files to delete
- None.

## Do not touch
- `skillforge/forge.py` and the forge path; do NOT register via `forge.register_tool`
- The excluded set, sub-agents, composers, the source artifacts

## Tool description
"Export or convert a produced artifact to a shareable format and return a download
link — the architecture diagram to a PNG image, or a spreadsheet (e.g. the BOM) to
CSV. Use when the user asks for an image/PNG of the diagram, a CSV of a sheet, or an
exported/shareable copy of an artifact."

## Acceptance criteria
- Exporting a stored `.xlsx` (e.g. the BOM) to CSV returns `ok` with a new
  `artifact_key` whose content is the sheet as CSV. (assert with a fixture workbook)
- Exporting the diagram to PNG returns `ok` with a PNG key when the CLI is present,
  or `status="error"` ("PNG export CLI unavailable") when it is not — never a crash. (assert)
- Unknown type/format → `needs_input`/`error`, no crash. (assert)
- `export_artifact` in the native tool list; NOT on the forge path →
  `pytest -m "not live"` green, forge unchanged.
- New tests green → `pytest tests/test_export_tools.py -m "not live"`.

# Task: /api/chat/background honors agent_mode
Phase: 5
Status: done

## Goal
The background chat endpoint was hardwired to forge: it constructed a forge via
`archie_session._get_forge(...)` and called `forge.run_turn_background(...)`
without ever consulting `agent_mode` — so background chat silently ran the forge
path even when native mode was selected.

Authorized by PLAN.md Decision #7 (native loop) — productionization; forge path
byte-for-byte unchanged.

## Files changed
- `server/routes/chat.py` — the background route branches on
  `archie_session.get_agent_mode()`:
  - `"native"` → `asyncio.create_task` of a native runner that awaits
    `archie_native_loop.run_turn(...)` (already a dict; no TurnResult conversion),
    does NOT re-save conversation turns (native `run_turn` persists them itself —
    the forge path saves in `on_complete`; double-saving would duplicate history),
    then mirrors the stream path's post-processing: `persist_bom_xlsx_downloads`
    (no-op for native BOMs), `build_artifact_manifest`,
    `attach_artifact_delivery_to_reply`, `update_latest_assistant_turn` (attach the
    manifest to the saved turn), `complete_job` with the same payload shape as the
    forge path; `fail_job(job_id, str(exc))` on exception.
  - `"forge"` (default) → the existing path, unchanged.

## Acceptance criteria (asserted in tests/test_background_job.py)
- With `get_agent_mode() == "native"`: the job completes with the native result
  payload, the native loop receives the request, and the forge is NEVER
  constructed (`_get_forge` patched to raise).
- With `get_agent_mode() == "forge"`: existing behavior byte-for-byte (regression
  test).
- Full suite → `pytest -m "not live"` green.

# Task: native streaming events + error isolation + artifact-type map
Phase: 5
Status: done

## Goal
Make native mode usable by real users in the chat UI. Before this task, the native
loop returned `events: []`, never fired `notifications.notify`, and the
`reasoning_sink` passed by the streaming path was dropped by the dispatcher — so
the UI showed nothing until the final reply (no thinking status, no live tool
chips). Additionally, one tool exception aborted the whole turn as an HTTP error,
and the artifact-type map covered only 3 of the 12 generate tools.

Key design point: `agent/chat_stream.py` already wires the two live hooks around
the orchestrator call — `reasoning_sink(label, phase)` → `thinking` events and
`notification_sink` converting `notify("tool_started:<tool>", customer_id)` →
live tool chips. Zero changes to chat_stream.py or the UI; the native loop just
calls the same hooks forge does.

Authorized by PLAN.md Decision #7 (native loop) — productionization of the native
path; forge path untouched.

## Files changed
- `agent/archie_session.py` — new `get_agent_mode()` helper (single source of
  truth for the config read); dispatcher passes `reasoning_sink` through to the
  native loop.
- `agent/archie_native_loop.py` —
  - `run_turn(..., reasoning_sink=None)`: emits a `thinking` label before every
    inference round; `_emit_reasoning` swallows sink errors (a sink failure must
    never break a turn).
  - `_notify_tool_started`: fires `notify(f"tool_started:{tool}", customer_id)`
    before each tool dispatch (no-ops without an active sink; failures logged,
    never raised).
  - Returned `events` now populated with `{"type": "native_reasoning"|
    "native_tool", "message", "data"}` dicts (unknown types are safely ignored by
    chat_stream's post-hoc translator; gives background/non-streaming parity).
  - **Per-tool error isolation**: the whole tool-execution block is wrapped; an
    exception produces a `status="error"` ToolResult the model sees via the
    NO-ARTIFACT banner and can adapt to — the turn never aborts. Per-turn repeat
    suppression prevents identical retry loops.
  - **Complete artifact-type map** `_ARTIFACT_TYPE_BY_TOOL` for all 12 registered
    generate tools (tests assert full coverage) so `get_document`/`list_artifacts`
    find every native-produced artifact.

## Acceptance criteria (all asserted in tests/test_archie_native_loop.py)
- reasoning_sink receives (label, "native_reasoning") calls during a turn; a
  broken sink does not break the turn.
- `notify` fires `tool_started:<tool>` per dispatched tool.
- `events` populated with type/message/data dicts including the tool trace.
- A raising handler → turn completes with an `"error"` tool_call, no exception.
- `_ARTIFACT_TYPE_BY_TOOL` covers exactly the 12 registered generate tools.
- Dispatcher passes `reasoning_sink` to the native loop.
- Forge mode unchanged → `pytest -m "not live"` green.

## Live follow-up (not verifiable from the implementation sandbox)
When stack access returns: run one native-mode chat in the UI and confirm live
thinking + tool chips appear mid-turn.

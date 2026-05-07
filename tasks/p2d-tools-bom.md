# Task p2d: Extract generate_bom Tool Handler

## Goal

Create `agent/tools/bom.py` with a `BomHandler` class that implements the
full `generate_bom` pipeline as a ToolHandler. This exercises the most
important framework seam: memory_contract + sub-agent call + safety check.

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/notes.py
pytest tests/test_tools_notes.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/tools/bom.py`
- `tests/test_tools_bom.py`

**Do NOT touch:**

- `agent/archie_loop.py` — leave `_execute_tool` intact
- `agent/archie_memory.py`
- Any existing file

## What to implement

### `agent/tools/bom.py`

The `generate_bom` branch in `_execute_tool` does this (in order):
1. Calls `archie_memory._prepare_bom_tool_args(args, user_message, context, decision_context)`
2. Calls `archie_memory._hydrate_tool_args_from_context(tool_name, args, context, decision_context, user_message)`
3. Calls `archie_memory._enforce_memory_contract_on_tool_args(tool_name, args, context)`
4. Checks for a direct-reply shortcut: `if args.get("_bom_direct_reply")` → return early with that message
5. Calls `_execute_tool_core("generate_bom", ...)` which calls the BOM sub-agent
6. Post-processes result (adds bom_context_source, grounding metadata)

The new handler replicates this pipeline. Because it needs `store`, `customer_id`,
`customer_name`, `user_message`, and `text_runner`, use a class pattern:

```python
class BomHandler:
    def __init__(
        self,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
        text_runner: Any,
        a2a_base_url: str = "",
    ) -> None: ...

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult: ...
```

Inside `__call__`:

**Step 1 — Context grounding (using existing archie_memory functions)**
```python
import agent.archie_memory as archie_memory

ctx = context  # already loaded by Forge from ArchieMemory.raw
args = archie_memory._prepare_bom_tool_args(
    args=args,
    user_message=self._last_user_message,   # captured from context or passed via args
    context=ctx,
    decision_context=memory.decision_context if memory else {},
)
args = archie_memory._hydrate_tool_args_from_context(
    tool_name="generate_bom",
    args=args,
    context=ctx,
    decision_context=memory.decision_context if memory else {},
    user_message=self._last_user_message,
)
args = archie_memory._enforce_memory_contract_on_tool_args(
    tool_name="generate_bom",
    args=args,
    context=ctx,
)
```

**Step 2 — Direct reply shortcut**
```python
if args.get("_bom_direct_reply"):
    return ToolResult(
        summary=str(args["_bom_direct_reply"]),
        status="needs_input",
        clarification=str(args["_bom_direct_reply"]),
    )
```

**Step 3 — Sub-agent call**
```python
body = await sub_agent_client.call_sub_agent(
    "bom",
    task=str(args.get("prompt") or ""),
    engagement_context=memory.raw if memory else {},
    trace_id=trace_id,
)
```

**Step 4 — Parse response**
```python
if body.get("status") == "needs_input":
    return ToolResult(
        summary=str(body.get("result") or "BOM needs more input."),
        status="needs_input",
        clarification=str(body.get("result") or ""),
    )
# Parse bom_payload from body["result"] (JSON string)
bom_payload = json.loads(body.get("result") or "{}")
artifact_key = ""   # BOM xlsx persistence handled by bom_service; key is in bom_payload
return ToolResult(
    summary="BOM generated with structured payload.",
    status="ok",
    data={
        "bom_payload": bom_payload,
        "bom_context_source": str(args.get("_bom_context_source") or "direct_request"),
    },
    artifact_key=str(bom_payload.get("xlsx_key") or ""),
)
```

### How to pass user_message into the handler

`user_message` is not in the ToolHandler signature. The pattern:
The handler reads `args.get("_user_message")` which `archie_wiring.py` will
pre-populate before calling Forge. Alternatively, capture it from
`memory.raw.get("last_user_message")` if ArchieMemory stores it.

For this task: read `user_message` from `args.get("_user_message", "")`.
Document this in a comment. `archie_wiring.py` will inject it.

## Test: `tests/test_tools_bom.py`

Use monkeypatch to stub `sub_agent_client.call_sub_agent` and
`archie_memory._prepare_bom_tool_args` / `_hydrate_tool_args_from_context` /
`_enforce_memory_contract_on_tool_args` (stub them to return args unchanged).

1. `test_bom_ok`
   Stub sub_agent to return `{"status": "ok", "result": '{"bom_payload": {"line_items": [], "totals": {"estimated_monthly_cost": 500}}}'}`.
   Assert `result.status == "ok"` and `result.data["bom_payload"]["totals"]["estimated_monthly_cost"] == 500`.

2. `test_bom_needs_input`
   Stub sub_agent to return `{"status": "needs_input", "result": "Please provide OCPU count."}`.
   Assert `result.status == "needs_input"` and `result.clarification == "Please provide OCPU count."`.

3. `test_bom_direct_reply_shortcut`
   Return args with `_bom_direct_reply="This is a followup without context."` from
   the `_prepare_bom_tool_args` stub.
   Assert `result.status == "needs_input"` and sub_agent was NOT called.

4. `test_bom_sub_agent_error`
   Stub sub_agent to raise `SubAgentError("connection refused")`.
   Assert `result.status == "blocked"`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/tools/bom.py` exits 0
2. `pytest tests/test_tools_bom.py -v` — 4 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "archie_loop" agent/tools/bom.py` — no matches

## Do NOT Do

- Do not modify `agent/archie_loop.py`
- Do not rewrite or simplify `_prepare_bom_tool_args` — call it as-is
- Do not move functions out of `agent/archie_memory.py`

## Commit Message

```
p2d: extract generate_bom tool handler
```

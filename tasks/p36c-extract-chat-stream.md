# Task p36c: Extract Streaming Chat Assembly from api_chat_stream

## Goal

`api_chat_stream` in `drawing_agent_server.py` (~203 lines starting at line 3651)
contains SSE/NDJSON streaming protocol implementation, status queue management,
background task orchestration, tool event formatting, and result transformation
— all inline in a FastAPI route handler.

Extract the streaming assembly logic into `agent/chat_stream.py`. The FastAPI
handler becomes a thin wrapper that calls into the new module. No behavior
change — the same SSE events are emitted in the same order with the same format.

---

## Prerequisite Check

```bash
python3.11 -m compileall drawing_agent_server.py
wc -l drawing_agent_server.py
pytest tests/ -q --tb=short 2>&1 | tail -5
```

All must pass. p36a must be merged before this task.

---

## Scope

**Only modify:**
- `drawing_agent_server.py` — replace `api_chat_stream` body with delegate
- `agent/chat_stream.py` — new file, streaming assembly logic

**Do NOT touch any other file.**

---

## What to implement

### 1. Create `agent/chat_stream.py`

Move the streaming logic out of the handler. The public interface:

```python
async def stream_chat_turn(
    *,
    customer_id: str,
    customer_name: str,
    message: str,
    store: "ObjectStoreBase",
    text_runner,
    a2a_base_url: str = "",
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields NDJSON-encoded SSE event strings.

    Yields one JSON line per event:
      {"type": "status", "message": "..."}
      {"type": "tool_call", "tool": "...", "args": {...}}
      {"type": "tool_result", "tool": "...", "summary": "..."}
      {"type": "artifact", "key": "...", "doc_type": "..."}
      {"type": "reply", "content": "..."}
      {"type": "error", "detail": "..."}
    """
```

Move the following from `api_chat_stream` into this function:
- Status queue setup and background task launch (`_run_orchestrator_turn` call)
- The `while True` polling loop that reads from the queue
- Event type dispatch (`status`, `tool_call`, `tool_result`, `artifact`, `reply`,
  `error` event construction)
- BOM XLSX persistence (`_persist_bom_xlsx_downloads`)
- Artifact manifest assembly (`_build_artifact_manifest`)
- Chat project membership persistence (`_persist_chat_project_membership`)
- Final `reply` event yield with artifacts

Keep the `asyncio.Queue`, `asyncio.create_task`, and `anyio`/`asyncio` imports
in the new module.

### 2. Replace handler body in `drawing_agent_server.py`

```python
@app.post("/api/chat/stream")
async def api_chat_stream(req: ChatRequest, _user: dict = Depends(require_user)):
    store = _require_object_store()
    return StreamingResponse(
        stream_chat_turn(
            customer_id=req.customer_id or req.client_id or "",
            customer_name=req.customer_name or "",
            message=req.message,
            store=store,
            text_runner=getattr(app.state, "text_runner", None),
            a2a_base_url=getattr(app.state, "a2a_base_url", ""),
        ),
        media_type="application/x-ndjson",
    )
```

Add the import:
```python
from agent.chat_stream import stream_chat_turn
```

---

## Acceptance Criteria

1. `python3.11 -m compileall drawing_agent_server.py agent/chat_stream.py` exits 0
2. `wc -l drawing_agent_server.py` — at least 170 lines fewer than after p36a
3. Handler `api_chat_stream` in `drawing_agent_server.py` is ≤ 12 lines
4. `ls agent/chat_stream.py` — exists
5. `grep "stream_chat_turn" agent/chat_stream.py` — matches
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Do NOT Do

- Do not change the NDJSON event format or field names — the UI parses these
- Do not change `_run_orchestrator_turn` — only move the code that calls it
- Do not merge this with p36b — they are independent and can run in parallel
- Do not remove `StreamingResponse` import from `drawing_agent_server.py`
  if other handlers use it

---

## Commit Message

```
p36c: extract streaming chat assembly into agent/chat_stream.py
```

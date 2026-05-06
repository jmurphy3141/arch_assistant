# Task p2c: Extract In-Process Tool Handlers (notes, summary, document)

## Goal

Create `agent/tools/notes.py` with three ToolHandler functions extracted from
`archie_loop._execute_tool()`: `save_notes`, `get_summary`, and `get_document`.
These are in-process tools (no sub-agent calls). They are the simplest extraction
and validate the handler pattern before tackling sub-agent tools.

## Prerequisite Check

```bash
python3.11 -c "from agent.archie_memory_impl import ArchieMemory; print('ok')"
pytest tests/test_archie_memory_impl.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/tools/__init__.py` (empty)
- `agent/tools/notes.py`
- `tests/test_tools_notes.py`

**Do NOT touch:**

- `agent/archie_loop.py` — leave `_execute_tool` intact
- `agent/archie_memory.py`
- Any existing file

## What to implement

### `agent/tools/notes.py`

Each function matches the `ToolHandler` protocol:
`async def handle(args, *, memory, context, trace_id) -> ToolResult`

**`save_notes_handle`**
Extract the `save_notes` branch from `_execute_tool_core()` in `archie_loop.py`.
- Get `text = str(args.get("text") or "")`. If empty, return
  `ToolResult(summary="No notes provided.", status="needs_input", clarification="Please provide note text.")`
- Call `document_store.save_note(store, customer_id, filename, text.encode())` to persist.
  But note: `store` and `customer_id` are not in the ToolHandler signature.
  Solution: `ArchieNotesHandler` is a class that takes `store` and `customer_id` in `__init__`
  and implements `__call__` matching ToolHandler. See example below.
- Call `archie_memory._record_saved_note_context(store, customer_id, customer_name, note_key, text, decision_context)`.
  Pass `decision_context=memory.decision_context if memory else {}`.
- Return `ToolResult(summary=f"Notes saved.", status="ok", artifact_key=note_key)`.

**`get_summary_handle`**
Extract the `get_summary` branch from `_execute_tool_core()`.
- Call `context_store.build_context_summary(context_store.read_context(store, customer_id, customer_name))`.
- Return `ToolResult(summary=summary_text, status="ok")` with `data={"summary": summary_text}`.

**`get_document_handle`**
Extract the `get_document` branch from `_execute_tool_core()`.
- Get `doc_type = str(args.get("type") or "")`.
- Call `document_store.get_latest_doc(store, customer_id, doc_type)`.
- If not found: return `ToolResult(summary=f"No {doc_type} document found.", status="needs_input", clarification=f"No {doc_type} found. Generate one first.")`.
- Return `ToolResult(summary=f"{doc_type} retrieved.", status="ok", artifact_key=key, data={"content": content})`.

### Handler class pattern for store/customer_id injection

Because ToolHandler signature doesn't include `store` or `customer_id`, use a
class with `__call__`:

```python
class NotesHandlers:
    def __init__(self, store: ObjectStoreBase, customer_id: str, customer_name: str):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def save_notes(self, args, *, memory, context, trace_id) -> ToolResult:
        ...

    async def get_summary(self, args, *, memory, context, trace_id) -> ToolResult:
        ...

    async def get_document(self, args, *, memory, context, trace_id) -> ToolResult:
        ...
```

Then in `archie_wiring.py` (future task):
```python
notes = NotesHandlers(store=store, customer_id=customer_id, customer_name=customer_name)
forge.register_tool("save_notes", notes.save_notes)
forge.register_tool("get_summary", notes.get_summary)
forge.register_tool("get_document", notes.get_document)
```

## Test: `tests/test_tools_notes.py`

Use `monkeypatch` to stub `document_store` and `context_store`.

1. `test_save_notes_ok`
   Stub `document_store.save_note` returning `"notes/abc.md"`.
   Stub `archie_memory._record_saved_note_context` as no-op.
   Call `notes.save_notes({"text": "hello"}, memory=snap, context={}, trace_id="t")`.
   Assert `result.status == "ok"` and `result.artifact_key == "notes/abc.md"`.

2. `test_save_notes_empty_text`
   Call with `{"text": ""}`.
   Assert `result.status == "needs_input"`.

3. `test_get_summary_ok`
   Stub context_store to return a context blob; `build_context_summary` returns `"Summary text."`.
   Assert `result.status == "ok"` and `result.data["summary"] == "Summary text."`.

4. `test_get_document_found`
   Stub `document_store.get_latest_doc` returning `("docs/pov.md", "POV content")`.
   Call with `{"type": "pov"}`.
   Assert `result.status == "ok"` and `result.artifact_key == "docs/pov.md"`.

5. `test_get_document_not_found`
   Stub `document_store.get_latest_doc` returning `(None, None)`.
   Assert `result.status == "needs_input"`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/tools/notes.py` exits 0
2. `pytest tests/test_tools_notes.py -v` — 5 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "_execute_tool\|archie_loop" agent/tools/notes.py` — no matches

## Do NOT Do

- Do not modify `agent/archie_loop.py`
- Do not add imports of `agent.archie_loop` in any new file
- Do not remove or rename the `_execute_tool` branches in archie_loop.py

## Commit Message

```
p2c: extract in-process tool handlers (save_notes, get_summary, get_document)
```

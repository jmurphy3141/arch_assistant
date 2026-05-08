# Task p37f: Hat State UI Events

## Goal

Hat activations and drops are invisible to the user. Add `hat_activate` and
`hat_drop` NDJSON event types to the streaming chat, and render them in
`ChatInterface.tsx` as an "Active Expert" badge so users can see which expert
mode is currently live.

---

## Scope

**Modify:**
- `skillforge/forge.py` — yield `hat_activate` / `hat_drop` TurnEvents
- `agent/chat_stream.py` — handle the new event types in the stream dispatch
- `ui/src/components/ChatInterface.tsx` — render active hat badge

**Do NOT touch:** `drawing_agent_server.py`, memory modules, hat files,
or any other UI component.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py agent/chat_stream.py
grep "hat_activate\|hat_drop" skillforge/forge.py   # should be absent
grep "TurnEvent" skillforge/forge.py | head -5
```

---

## What to implement

### 1. Yield `hat_activate` / `hat_drop` events in `forge.py`

In the `run_turn` ReAct loop, find where hats are applied and dropped.

**After `apply_hat` succeeds:**
```python
active_hats = self._hat_engine.apply_hat(active_hats, hat_name)
yield TurnEvent(
    type="hat_activate",
    data={"hat": hat_name, "display_name": self._hat_engine.get_hat_meta(hat_name).get("display_name", hat_name)}
)
```

**After `drop_hat` succeeds:**
```python
active_hats = self._hat_engine.drop_hat(active_hats, hat_name)
yield TurnEvent(
    type="hat_drop",
    data={"hat": hat_name}
)
```

Also emit `hat_activate` for any hats auto-activated by `p37d` coordination rules.

### 2. Handle events in `agent/chat_stream.py`

In the event dispatch loop (the `while True` / `async for` that reads from the
Forge event queue), add cases for the new types:

```python
elif event_type == "hat_activate":
    hat = event_data.get("hat", "")
    display = event_data.get("display_name", hat)
    yield json.dumps({"type": "hat_activate", "hat": hat, "display_name": display}) + "\n"

elif event_type == "hat_drop":
    hat = event_data.get("hat", "")
    yield json.dumps({"type": "hat_drop", "hat": hat}) + "\n"
```

### 3. Render in `ChatInterface.tsx`

**State:**
```typescript
const [activeHats, setActiveHats] = useState<string[]>([]);
```

**In the event handler** (where `tool_call`, `status`, etc. are dispatched):
```typescript
} else if (event.type === 'hat_activate') {
  setActiveHats(prev => prev.includes(event.hat) ? prev : [...prev, event.hat]);
} else if (event.type === 'hat_drop') {
  setActiveHats(prev => prev.filter(h => h !== event.hat));
}
```

**Render a badge strip** above or below the streaming message area:
```tsx
{activeHats.length > 0 && (
  <div className="flex gap-2 px-4 py-1 text-xs text-slate-500">
    <span>Active:</span>
    {activeHats.map(hat => (
      <span key={hat} className="bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium">
        {hat.replace(/_/g, ' ')}
      </span>
    ))}
  </div>
)}
```

Clear `activeHats` when a new turn starts (on submit).

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py agent/chat_stream.py` exits 0
2. `grep "hat_activate" skillforge/forge.py` — matches
3. `grep "hat_activate" agent/chat_stream.py` — matches
4. `grep "hat_activate" ui/src/components/ChatInterface.tsx` — matches
5. `grep "activeHats" ui/src/components/ChatInterface.tsx` — at least 3 matches
6. `pytest tests/test_forge.py -q --tb=short` — same pass count

---

## Do NOT Do

- Do not change the NDJSON event format for existing event types
- Do not add hat events to the SSE stream format — NDJSON only
- Do not add hat state to the backend API response body

---

## Commit Message

```
p37f: hat_activate/hat_drop events — stream hat state to UI badge
```

Branch: `claude/p37f` (from main after p37a merges). Push when done.

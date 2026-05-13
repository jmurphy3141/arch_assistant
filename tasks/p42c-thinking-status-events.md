# Task p42c: "Thinking..." Visibility — Surface Reasoning Events to Chat UI

## Objective

Forge emits TurnEvents for every reasoning step (`step3_planning`,
`expert_pre_action`, `hat_auto_activated`, `expert_post_review`,
`pre_action_light`). These are currently invisible to the user — they exist
in logs and in `TurnResult.events` but the chat streaming layer discards them.

Wire these events through the streaming pipeline so the UI can show a brief
"Thinking..." status indicator while Archie is reasoning. This fulfills
Requirement 4 of the pre-action thinking spec (visibility).

---

## Scope

**Touch:**
- `agent/chat_stream.py` — add cases for reasoning TurnEvents in the
  post-turn event loop; yield `thinking` event dicts to the client
- `ui/src/components/ChatInterface.tsx` — display a dismissible
  "Thinking..." status line when `event_type === "thinking"` events arrive

**Do NOT touch:** `skillforge/forge.py`, `skillforge/types.py`, hat files,
other Python modules.

---

## Prerequisite Check

```bash
grep "thinking\|step3_planning\|expert_pre_action\|pre_action_light" agent/chat_stream.py
# must be zero
grep "event_type.*thinking\|thinking.*event" ui/src/components/ChatInterface.tsx
# must be zero
```

---

## Changes

### 1. `agent/chat_stream.py` — Add reasoning event forwarding

In `_chat_event_dicts()`, in the `for event in result.get("events", []):` loop
(currently at lines 86-106), add cases for reasoning event types after the
existing `hat_drop` case:

```python
            elif event_type in (
                "step3_planning",
                "expert_pre_action",
                "expert_post_review",
                "hat_auto_activated",
                "pre_action_light",
            ):
                # Surface reasoning steps as a "thinking" event for UI visibility.
                label_map = {
                    "step3_planning":      "Planning approach...",
                    "expert_pre_action":   "Expert pre-action analysis...",
                    "expert_post_review":  "Expert review...",
                    "hat_auto_activated":  f"Activating {event_data.get('hat', 'expert')} lens...",
                    "pre_action_light":    f"Pre-action check for {event_data.get('tool', 'tool')}...",
                }
                yield {
                    "trace_id": trace_id,
                    "customer_id": customer_id,
                    "event_type": "thinking",
                    "label": label_map.get(event_type, "Thinking..."),
                    "reasoning_type": event_type,
                }
```

### 2. `ui/src/components/ChatInterface.tsx` — Display "Thinking..." indicator

Locate where streaming events are handled (the switch/if block that processes
`event_type`). Add a case for `"thinking"`:

- On receipt of a `thinking` event, show a brief status line above the
  streaming reply area: the `label` field (e.g. "Planning approach...",
  "Expert pre-action analysis...", "Expert review...")
- Clear it when `event_type === "completion"` arrives
- Style: muted text (gray), small font, no persistent badge —
  it should disappear cleanly when the reply renders

Minimal implementation — a single line of muted text, no animation required.
Match the existing style of other status indicators in the component.

---

## Acceptance Criteria

1. Python compiles cleanly:
   ```bash
   python3.11 -m compileall agent/chat_stream.py
   ```

2. Reasoning event types handled in chat_stream:
   ```bash
   grep "step3_planning\|expert_pre_action\|hat_auto_activated\|thinking" agent/chat_stream.py | wc -l
   # must be ≥ 4
   ```

3. UI handles thinking event type:
   ```bash
   grep "thinking" ui/src/components/ChatInterface.tsx | wc -l
   # must be ≥ 2
   ```

4. TypeScript compiles (no new type errors):
   ```bash
   cd ui && npx tsc --noEmit
   ```

5. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p42c: surface reasoning TurnEvents as "Thinking..." status in chat UI
```

Branch: `claude/p42c` (from main, after p42a–p42b merged). Push when done.

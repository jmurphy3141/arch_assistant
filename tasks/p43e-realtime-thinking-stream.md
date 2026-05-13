# Task p43e: Real-Time Thinking Stream

## Objective

p42c surfaces "Thinking..." events after the turn completes (post-turn events
forwarded through `chat_stream.py`). Users wait silently for 30–60 seconds
with no feedback while step3_planning, expert pre-action, and post-review LLM
calls execute.

Add a `reasoning_sink` callback parameter to `Forge.run_turn()`. Before each
reasoning LLM call (step3_planning, expert_pre_action, expert_post_review),
Forge calls the sink with a label. The server layer wires the sink to push
into the existing real-time status queue, so the UI sees "Planning approach..."
appear live during the turn.

This uses the same `status_queue` / `loop.call_soon_threadsafe` pattern
already in `chat_stream.py` for tool notifications.

---

## Scope

**Touch:**
- `skillforge/forge.py` — add `reasoning_sink` param to `run_turn()` and
  the three reasoning methods; call sink before each reasoning LLM call
- `agent/archie_loop.py` — accept and pass `reasoning_sink` to `forge.run_turn()`
- `drawing_agent_server.py` — update `_run_orchestrator_turn()` to accept
  and pass `reasoning_sink`
- `agent/chat_stream.py` — create `_thinking_sink` in the notification task
  and pass it through to `_run_orchestrator_turn()`

**Do NOT touch:** hat files, skillforge/registry.py, skillforge/types.py,
other modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py agent/archie_loop.py \
  agent/chat_stream.py
grep "reasoning_sink\|_thinking_sink" skillforge/forge.py \
  agent/archie_loop.py agent/chat_stream.py drawing_agent_server.py
# must be zero matches
```

---

## Changes

### 1. `skillforge/forge.py`

#### 1a. Add `reasoning_sink` to `run_turn()` signature

```python
async def run_turn(
    self,
    *,
    session_id: str,
    user_message: str,
    context: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
    reasoning_sink: "Callable[[str, str], None] | None" = None,
) -> TurnResult:
```

(`reasoning_sink(label: str, phase: str) -> None` — two string args.)

#### 1b. Thread `reasoning_sink` into the three reasoning methods

Pass it at each call site:

```python
prompt = await self._run_step3_planning(
    ...,
    reasoning_sink=reasoning_sink,
)
```

```python
prompt, clarification_needed = await self._run_expert_pre_action(
    ...,
    reasoning_sink=reasoning_sink,
)
```

```python
prompt, review_decision = await self._run_expert_post_review(
    ...,
    reasoning_sink=reasoning_sink,
)
```

#### 1c. Add `reasoning_sink` param to each reasoning method and call it

In `_run_step3_planning()`, add `reasoning_sink=None` to signature. Before
`raw = await self._text_runner(...)`:

```python
        if reasoning_sink:
            reasoning_sink("Planning approach...", "step3_planning")
```

In `_run_expert_pre_action()`, add `reasoning_sink=None` to signature. Before
`raw = await self._text_runner(...)` (the main call, not the retry):

```python
        if reasoning_sink:
            reasoning_sink("Expert pre-action analysis...", "expert_pre_action")
```

In `_run_expert_post_review()`, add `reasoning_sink=None` to signature. Before
`raw = await self._text_runner(...)`:

```python
        if reasoning_sink:
            reasoning_sink("Expert review...", "expert_post_review")
```

### 2. `agent/archie_loop.py`

Find the `forge_result = await forge.run_turn(...)` call (around line 910).
Add `reasoning_sink` to the call:

```python
        forge_result = await forge.run_turn(
            session_id=customer_id,
            user_message=user_message,
            context=context,
            history=history,
            reasoning_sink=kwargs.get("reasoning_sink"),
        )
```

Also update the `run_turn()` function signature in `archie_loop.py` to accept
and thread `**kwargs` or an explicit `reasoning_sink=None` parameter, whichever
is cleaner given the existing signature. Read the function signature at line
198 before deciding.

### 3. `drawing_agent_server.py`

Find `_run_orchestrator_turn()`. Add `reasoning_sink=None` parameter and pass
it through to `archie_loop.run_turn()` (or however that function is called).

Read the function to understand its exact call pattern before modifying.

### 4. `agent/chat_stream.py`

In `_run_orchestrator_with_stream_notifications()`, add a `_thinking_sink`
alongside the existing `_sink`:

```python
        def _thinking_sink(label: str, phase: str) -> None:
            payload = {
                "trace_id": trace_id,
                "customer_id": customer_id,
                "event_type": "thinking",
                "label": label,
                "reasoning_type": phase,
            }
            loop.call_soon_threadsafe(queue.put_nowait, payload)
```

Then pass it to `_run_orchestrator_turn`:

```python
        result = await server._run_orchestrator_turn(
            req=req,
            store=store,
            text_runner=text_runner,
            orch_cfg=server._cfg.get("orchestrator", {}),
            reasoning_sink=_thinking_sink,
        )
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py agent/archie_loop.py \
     agent/chat_stream.py drawing_agent_server.py
   ```

2. `reasoning_sink` wired through all four files:
   ```bash
   grep "reasoning_sink\|_thinking_sink" skillforge/forge.py \
     agent/archie_loop.py agent/chat_stream.py drawing_agent_server.py | wc -l
   # must be ≥ 8
   ```

3. Sink called before each reasoning phase:
   ```bash
   grep "reasoning_sink\|Planning approach\|Expert pre-action\|Expert review" \
     skillforge/forge.py | wc -l
   # must be ≥ 6
   ```

4. No-op when reasoning_sink=None (existing behaviour unchanged):
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

5. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p43e: real-time thinking stream — reasoning_sink pushes live Thinking... events to UI
```

Branch: `claude/p43e` (from main, after p43a–p43d merged). Push when done.

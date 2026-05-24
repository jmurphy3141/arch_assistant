# Task p55f — Background Job Cleanup (Post-p55a Review)

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/p55f
**Depends on:** p55a merged (PR #229)
**Fixes:** Three issues found in p55a code review

---

## Context

p55a (PR #229) shipped background job support. Code review identified three issues:

1. **`Forge.run_turn_background()` is dead code** — the `/api/chat/background`
   endpoint has its own inline `_run_background_chat()` coroutine that calls
   `_run_orchestrator_turn()` directly, bypassing `forge.run_turn_background()`
   entirely. The method was added to `skillforge/forge.py` but nothing calls it.

2. **`_load_telegram_config()` is called twice per notification** — once in
   `_send()` to check `enabled`, and again inside `_send_telegram()` to get
   the token/chat_id env var names. The YAML file is read from disk twice on
   every notification event.

3. **Background mode stays on after job completes** — when a background job
   finishes (success or error), `setBackgroundJobId(null)` clears the pill but
   `backgroundMode` stays `true`. The next message silently goes to background
   without the SE re-toggling. Should be one-shot.

---

## What to Build

### Fix 1 — Wire the background endpoint through `forge.run_turn_background()`

**`drawing_agent_server.py`**

The existing `_run_background_chat()` inner function calls `_run_orchestrator_turn()`
then does artifact persistence inline. Restructure it to call
`forge.run_turn_background()` and move the artifact persistence steps into the
`on_complete` callback.

Before:
```python
async def _run_background_chat() -> None:
    try:
        result = await _run_orchestrator_turn(req=req, ...)
        result = await _persist_bom_xlsx_downloads(...)
        artifact_manifest = _build_artifact_manifest(...)
        ...
        _complete_job(job_id, payload)
        notify(...)
    except Exception as exc:
        _fail_job(job_id, str(exc))
```

After:
```python
session = _get_or_create_archie_session(req.customer_id, store, text_runner, ...)

async def on_complete(result: TurnResult) -> None:
    result_dict = _turn_result_to_dict(result)
    result_dict = await _persist_bom_xlsx_downloads(req.customer_id, store, result_dict)
    artifact_manifest = _build_artifact_manifest(req.customer_id, result_dict)
    project_membership = _persist_chat_project_membership(store, req)
    payload = {
        "status": "ok",
        "reply": result_dict["reply"],
        "tool_calls": result_dict["tool_calls"],
        "artifacts": result_dict["artifacts"],
        "artifact_manifest": artifact_manifest,
        "project_id": project_membership["project_id"],
        "project_name": project_membership["project_name"],
        "engagement_id": req.customer_id,
        "history_length": result_dict["history_length"],
        "trace_id": _current_trace_id(),
    }
    _complete_job(job_id, payload)
    try:
        notify("poc_step_complete", req.customer_id, detail=str(result_dict.get("reply", ""))[:200])
    except Exception:
        logger.exception("Background chat notification failed job_id=%s", job_id)

async def on_error(exc: Exception) -> None:
    _fail_job(job_id, str(exc))

asyncio.create_task(
    session.forge.run_turn_background(
        message=req.message,
        history=session.history,
        context=session.context,
        on_complete=on_complete,
        on_error=on_error,
        session_id=req.customer_id,
    )
)
```

Note: study how `_run_orchestrator_turn()` obtains the session (history, context,
forge instance) before implementing — do not duplicate that logic, reuse existing
session helpers.

### Fix 2 — Pass config through to avoid double disk read

**`agent/notifications.py`**

Load config once in `_send()` and pass it to `_send_telegram()`:

```python
def _send(event: str, customer_id: str, detail: str) -> None:
    logger.info("NOTIFY event=%s customer_id=%s detail=%r", event, customer_id, detail)
    cfg = _load_telegram_config()
    if not cfg.get("enabled", False):
        return
    message = _format_message(event, customer_id, detail)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_send_telegram(message, cfg))


async def _send_telegram(message: str, cfg: dict) -> None:
    token_env = cfg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    chat_id_env = cfg.get("chat_id_env", "TELEGRAM_CHAT_ID")
    token = os.environ.get(str(token_env), "")
    chat_id = os.environ.get(str(chat_id_env), "")
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            )
    except Exception:
        logger.debug("Telegram notification failed", exc_info=True)
```

### Fix 3 — Reset background mode to one-shot on job completion

**`ui/src/components/ChatInterface.tsx`**

In the `useEffect` polling callback, call `setBackgroundMode(false)` when the
job reaches a terminal state (complete or error):

```typescript
// On success:
setMessages(prev => [...prev, assistantMsg]);
setBackgroundJobId(null);
setBackgroundMode(false);   // ← add this

// On error:
setError(`Background job failed: ${e.detail ?? 'unknown error'}`);
setBackgroundJobId(null);
setBackgroundMode(false);   // ← add this
```

---

## Constraints

- Fix 1 changes `drawing_agent_server.py` only — do not modify `skillforge/forge.py`
  (the method signature is already correct)
- Fix 2 changes `agent/notifications.py` only — update the call site for
  `_send_telegram` in tests if the signature changes
- Fix 3 changes `ui/src/components/ChatInterface.tsx` only — two `setBackgroundMode(false)`
  additions in the existing polling effect

---

## Acceptance Criteria

```bash
# Fix 1
python3.11 -m compileall drawing_agent_server.py
grep "run_turn_background" drawing_agent_server.py  # must show call site, not just method

# Fix 2
python3.11 -m compileall agent/notifications.py
grep "_send_telegram" agent/notifications.py        # must show single call with cfg arg

# Fix 3
cd ui && npm run typecheck

# Tests
pytest tests/test_background_job.py -v
```

---

## Commit Message

```
p55f: background job cleanup — wire run_turn_background, single config load, one-shot mode
```

**Branch:** `claude/p55f` (from main, after p55a merged). Push when done.

# Codex Agent Prompt — Issue 1: Background Chat Job Support + Telegram Notification

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/explore-repo-Os53i
**Requirements:** docs/requirements-poc-workflow.md FR-2.*

---

## Task

Add background job support to the chat endpoint so SEs can kick off POC generation during a meeting and receive a Telegram notification when work completes.

---

## Context

- FastAPI server: `drawing_agent_server.py`
- Background job infrastructure **already exists** at lines 334–360:
  - `_JOB_STORE: Dict[str, dict]` — in-memory store with TTL
  - `_new_job()` → returns UUID job_id
  - `_complete_job(jid, result)` → sets status "complete"
  - `_fail_job(jid, detail)` → sets status "error"
- The polling endpoint `GET /api/job/{job_id}` already exists at ~line 2436
- Telegram notification stub at `agent/notifications.py` lines 62–75 — has a TODO comment, is not yet implemented
- Forge orchestrator: `skillforge/forge.py` — owns the turn loop; `run_turn()` is the primary method
- Session wrapper: `agent/archie_session.py` — thin wrapper (~150 lines); MUST NOT contain new routing logic
- Architecture: Forge = domain-agnostic (changes go here). Archie = OCI personality (no Forge logic there).

---

## What to Build

### 1. `skillforge/forge.py` — Add `run_turn_background()`

Add an async method that wraps `run_turn()` for background use:

```python
async def run_turn_background(
    self,
    message: str,
    history: list,
    context: dict,
    on_complete: Callable[[TurnResult], Awaitable[None]],
    on_error: Callable[[Exception], Awaitable[None]],
) -> None:
    try:
        result = await self.run_turn(message, history, context)
        await on_complete(result)
    except Exception as e:
        await on_error(e)
```

No Archie-specific logic here. The callbacks are injected by the caller.

### 2. `drawing_agent_server.py` — New endpoint `POST /api/chat/background`

```python
@app.post("/api/chat/background", status_code=202)
async def chat_background(request: ChatRequest):
    job_id = _new_job()

    async def on_complete(result: TurnResult):
        _complete_job(job_id, {"reply": result.reply, "artifacts": result.artifacts})
        await notifications.notify("poc_step_complete", request.customer_id, detail=result.reply[:200])

    async def on_error(e: Exception):
        _fail_job(job_id, str(e))

    session = _get_or_create_session(request.customer_id)
    asyncio.create_task(
        session.forge.run_turn_background(
            message=request.message,
            history=session.history,
            context=session.context,
            on_complete=on_complete,
            on_error=on_error,
        )
    )
    return {"job_id": job_id, "status": "pending"}
```

### 3. `agent/notifications.py` — Implement Telegram call

Replace the TODO stub (lines 73–75) with:

```python
import httpx

async def _send_telegram(message: str) -> None:
    token = os.environ.get(cfg.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
    chat_id = os.environ.get(cfg.get("telegram_chat_id_env", "TELEGRAM_CHAT_ID"), "")
    if not token or not chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            )
    except Exception:
        pass  # fire-and-forget
```

### 4. `config.yaml` — Add Telegram config

```yaml
telegram:
  enabled: false
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  chat_id_env: "TELEGRAM_CHAT_ID"
```

### 5. `ui/src/components/ChatInterface.tsx` — Background mode indicator

- Add a "Background" toggle button in the chat input area
- When toggled on: POST to `/api/chat/background` instead of connecting SSE
- Show a dismissible pill: "Working in background... [job_id]"
- Poll `GET /api/job/{job_id}` every 5 seconds
- When status becomes `complete`: append reply to chat history, clear pill
- When status becomes `error`: show error toast, clear pill

---

## Constraints

- Do NOT add routing logic, LLM calls, or tool dispatch to `archie_session.py`
- Reuse `_new_job()`, `_complete_job()`, `_fail_job()` exactly as-is — do not refactor them
- Telegram send must be fire-and-forget: failure must not affect job completion
- Background jobs must not block the existing SSE streaming path
- Use `asyncio.create_task()` — not `threading.Thread`

---

## Tests

Create `tests/test_background_job.py`:

```python
async def test_background_endpoint_returns_202_with_job_id():
    # POST /api/chat/background → 202, body has job_id

async def test_background_job_transitions_pending_to_complete():
    # Start job, mock archie_session.run_turn to return a TurnResult
    # Poll until complete, assert reply in result

async def test_telegram_notify_fires_when_enabled(httpx_mock):
    # Enable telegram in config, trigger notify()
    # Assert httpx_mock received call to api.telegram.org

async def test_telegram_failure_does_not_fail_job():
    # httpx throws, job still completes
```

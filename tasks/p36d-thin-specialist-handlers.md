# Task p36d: Thin POV, WAF, and JEP Generate Handlers

## Goal

`pov_generate`, `waf_generate`, and `jep_generate` in `drawing_agent_server.py`
each contain inline runner construction — building a `def runner(prompt, ...):`
closure that calls `run_inference` directly, then passing it to the agent
function. This pattern is duplicated identically across all three handlers.

`agent/tools/specialists.py` already exists. This task moves the runner
construction into a shared helper in `specialists.py`, then replaces the
duplicated runner closures in each handler with a single call to that helper.

`jep_generate` additionally contains JEP lifecycle state-machine logic — that
stays in `drawing_agent_server.py` for now (deferred to a future task).

---

## Prerequisite Check

```bash
python3.11 -m compileall drawing_agent_server.py agent/tools/specialists.py
wc -l drawing_agent_server.py
pytest tests/ -q --tb=short 2>&1 | tail -5
```

All must pass. p36a must be merged before this task.

---

## Scope

**Only modify:**
- `drawing_agent_server.py` — replace duplicated runner closures with helper call
- `agent/tools/specialists.py` — add `build_inference_runner()` helper

**Do NOT touch `agent/pov_agent.py`, `agent/jep_agent.py`, `agent/waf_agent.py`,
or any other file.**

---

## Duplicated pattern to eliminate

All three handlers contain this identical pattern (with same parameters):

```python
def _run_X():
    def runner(prompt, system_message=""):
        from agent.llm_inference_client import run_inference as _ri
        return _ri(
            prompt,
            endpoint=INFERENCE_ENDPOINT,
            model_id=INFERENCE_MODEL_ID,
            compartment_id=COMPARTMENT_ID,
            max_tokens=WRITING_MAX_TOKENS,
            temperature=WRITING_TEMPERATURE,
            top_p=WRITING_TOP_P,
            top_k=WRITING_TOP_K,
            system_message=system_message,
        )
    text_runner = getattr(app.state, "text_runner", None) or runner
    return generate_X(...)
```

---

## What to implement

### 1. Add `build_inference_runner()` to `agent/tools/specialists.py`

```python
def build_inference_runner(app_state, *, inference_config: dict):
    """
    Return a text_runner callable using app.state if available,
    otherwise build one from inference_config.

    inference_config keys: endpoint, model_id, compartment_id,
    max_tokens, temperature, top_p, top_k.
    """
    existing = getattr(app_state, "text_runner", None)
    if existing:
        return existing

    def runner(prompt, system_message=""):
        from agent.llm_inference_client import run_inference as _ri
        return _ri(
            prompt,
            endpoint=inference_config["endpoint"],
            model_id=inference_config["model_id"],
            compartment_id=inference_config["compartment_id"],
            max_tokens=inference_config.get("max_tokens", 4096),
            temperature=inference_config.get("temperature", 0.7),
            top_p=inference_config.get("top_p", 0.9),
            top_k=inference_config.get("top_k", 50),
            system_message=system_message,
        )
    return runner
```

### 2. In `drawing_agent_server.py`, build a module-level inference config dict

After the config constants are defined (around line 200–300), add:

```python
_WRITING_INFERENCE_CONFIG = {
    "endpoint": INFERENCE_ENDPOINT,
    "model_id": INFERENCE_MODEL_ID,
    "compartment_id": COMPARTMENT_ID,
    "max_tokens": WRITING_MAX_TOKENS,
    "temperature": WRITING_TEMPERATURE,
    "top_p": WRITING_TOP_P,
    "top_k": WRITING_TOP_K,
}
```

### 3. Replace runner construction in `pov_generate`, `waf_generate`

Replace the `def _run_pov():` / `def _run_waf():` closures with:

```python
# pov_generate
text_runner = build_inference_runner(app.state, inference_config=_WRITING_INFERENCE_CONFIG)
result = await anyio.to_thread.run_sync(
    functools.partial(generate_pov, req.customer_id, req.customer_name, store, text_runner,
                      feedback=req.feedback or "")
)
```

```python
# waf_generate
text_runner = build_inference_runner(app.state, inference_config=_WRITING_INFERENCE_CONFIG)
result = await anyio.to_thread.run_sync(
    functools.partial(generate_waf, req.customer_id, req.customer_name, store, text_runner,
                      feedback=req.feedback or "")
)
```

### 4. Replace runner construction in `jep_generate`

Same pattern — replace only the runner closure. Leave all JEP lifecycle logic
(`jep_lifecycle`, state machine, approval checks) intact.

Add import at top of `drawing_agent_server.py`:
```python
from agent.tools.specialists import build_inference_runner
```

---

## Acceptance Criteria

1. `python3.11 -m compileall drawing_agent_server.py agent/tools/specialists.py` exits 0
2. `wc -l drawing_agent_server.py` — at least 50 lines fewer than after p36a
3. `grep "build_inference_runner" agent/tools/specialists.py` — matches
4. `grep -c "def runner(prompt, system_message" drawing_agent_server.py` — 0
5. `grep "build_inference_runner" drawing_agent_server.py` — at least 3 matches
   (one per handler: pov, waf, jep)
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Do NOT Do

- Do not extract the JEP lifecycle state machine in this task — that is future work
- Do not change the HTTP response shape of any handler
- Do not modify `agent/pov_agent.py`, `agent/jep_agent.py`, `agent/waf_agent.py`
- Do not merge with p36b or p36c — all three can run in parallel after p36a

---

## Commit Message

```
p36d: eliminate duplicated inference runner closures in POV/WAF/JEP handlers
```

# Task p36b: Extract terraform_generate Handler Logic

## Goal

The `terraform_generate` handler in `drawing_agent_server.py` (~156 lines,
starting ~line 4887) contains context hydration, prompt synthesis, sub-agent
dispatch, JSON result parsing, file aliasing, fallback bundle generation,
persistence, and context recording — all inline in a FastAPI route handler.

`agent/tools/terraform.py` already exists with a `TerraformHandler` class that
wraps `sub_agent_client` and `document_store`. However, the route handler
reimplements this logic independently with different prompt assembly and response
shaping.

This task extracts the `terraform_generate` handler's business logic into a
standalone async function in `agent/tools/terraform.py`, then replaces the
handler with a thin delegate call.

---

## Prerequisite Check

```bash
python3.11 -m compileall drawing_agent_server.py agent/tools/terraform.py
wc -l drawing_agent_server.py
pytest tests/ -q --tb=short 2>&1 | tail -5
```

All must pass. If p36a is not merged, stop — run p36a first.

---

## Scope

**Only modify:**
- `drawing_agent_server.py` — replace handler body with delegate call
- `agent/tools/terraform.py` — add `generate_terraform_bundle()` async function

**Do NOT touch any other file.**

---

## What to implement

### 1. Add `generate_terraform_bundle()` to `agent/tools/terraform.py`

Extract the full logic from the `terraform_generate` handler into a standalone
async function. The function signature:

```python
async def generate_terraform_bundle(
    *,
    customer_id: str,
    customer_name: str,
    prompt: str,
    store: "ObjectStoreBase",
    trace_id: str,
) -> dict:
    """
    Orchestrate Terraform generation: context hydration, sub-agent call,
    result parsing, persistence, context recording.

    Returns the response dict ready to be returned from the FastAPI handler.
    Raises HTTPException on terminal failures.
    """
```

Move the following from the handler into this function:
- `read_context` / `get_new_notes` / `build_context_summary` calls
- Default prompt construction (the block starting with `"Generate a complete OCI Terraform..."`)
- `sub_agent_client.call_sub_agent(...)` invocation
- JSON result parsing and `file_aliases` mapping
- `_terraform_fallback_files()` fallback branch
- `save_terraform_bundle(...)` persistence
- `record_agent_run(...)` + `write_context(...)` context recording
- Return value construction

The `need_clarification` early return stays — return a dict with
`"status": "need_clarification"` and the handler maps it to an HTTP response.

Import `anyio` and `functools` inside the function (they are already available
in `drawing_agent_server.py` — add the imports to `terraform.py` as needed).

### 2. Replace handler body in `drawing_agent_server.py`

The `terraform_generate` handler (~line 4887) becomes:

```python
@app.post("/api/terraform/generate")
async def terraform_generate(req: TerraformGenerateRequest):
    """Generate Terraform bundle using the Terraform sub-agent."""
    store = _require_object_store()
    result = await generate_terraform_bundle(
        customer_id=req.customer_id,
        customer_name=req.customer_name,
        prompt=req.prompt or "",
        store=store,
        trace_id=_current_trace_id(),
    )
    return result
```

Add the import at the top of `drawing_agent_server.py`:
```python
from agent.tools.terraform import generate_terraform_bundle
```

---

## Acceptance Criteria

1. `python3.11 -m compileall drawing_agent_server.py agent/tools/terraform.py` exits 0
2. `wc -l drawing_agent_server.py` — at least 130 lines fewer than after p36a
3. `grep -c "def terraform_generate" drawing_agent_server.py` — output is `1`
4. Handler body in `drawing_agent_server.py` is ≤ 10 lines
5. `grep "generate_terraform_bundle" agent/tools/terraform.py` — matches
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Do NOT Do

- Do not change the HTTP response shape — callers expect the same JSON fields
- Do not remove `_terraform_fallback_files()` from `drawing_agent_server.py`
  if it is used by other handlers
- Do not change `TerraformHandler` — it is used by `archie_loop` via SkillForge
- Do not modify the sub-agent protocol or `sub_agent_client.call_sub_agent()` signature

---

## Commit Message

```
p36b: extract terraform_generate handler logic into agent/tools/terraform.py
```

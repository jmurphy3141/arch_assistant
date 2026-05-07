# Phase 3.6 Codex Prompts

---

## Prompt 1 — p36a: Delete dead routes (run first, alone)

```
Implement tasks/p36a-delete-dead-routes.md exactly as written.

First run the prerequisite check:
  wc -l drawing_agent_server.py
  grep -c "^@app\." drawing_agent_server.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Record the line count before making any changes.

Then make two categories of changes:

CATEGORY 1 — Remove bare decorator lines only (keep the /api/* decorator and handler):
Delete only the @app.post or @app.get line for each bare alias listed in the spec.
Verify after each deletion that the /api/* decorator immediately below is still present.

The bare decorators to remove (one line each):
  Line ~1723: @app.post("/upload-to-bucket")
  Line ~1761: @app.post("/upload-bom")
  Line ~1906: @app.post("/clarify")
  Line ~2066: @app.post("/refine")
  Line ~2336: @app.post("/generate")
  Line ~4350: @app.post("/notes/upload")
  Line ~4377: @app.get("/notes/{customer_id}")
  Line ~4394: @app.post("/pov/generate")
  Line ~4447: @app.get("/pov/{customer_id}/latest")
  Line ~4460: @app.get("/pov/{customer_id}/versions")
  Line ~4473: @app.post("/jep/generate")
  Line ~4545: @app.get("/jep/{customer_id}/latest")
  Line ~4567: @app.get("/jep/{customer_id}/versions")
  Line ~4799: @app.post("/waf/generate")
  Line ~4856: @app.get("/waf/{customer_id}/latest")
  Line ~4874: @app.get("/waf/{customer_id}/versions")
  Line ~4887: @app.post("/terraform/generate")
  Line ~5045: @app.get("/terraform/{customer_id}/latest")
  Line ~5071: @app.get("/terraform/{customer_id}/versions")
  Line ~5087: @app.get("/context/{customer_id}")

CATEGORY 2 — Delete entire handler (decorator + function body):
  - @app.post("/chat") and its async def chat(...) body (~line 2410, ~10 lines)
  - @app.get("/mcp/tools") and its async def mcp_tools(...) body (~line 2695, ~56 lines)
  - @app.get("/mcp/tools/get_oci_catalogue") and its handler (~line 2751, ~12 lines)
  - @app.get("/.well-known/agent-card-legacy.json") and its handler (~line 2948, ~22 lines)

DO NOT touch: /message:send, /tasks/*, /health, /.well-known/agent.json, /.well-known/agent-card.json

Verify all acceptance criteria:
  python3.11 -m compileall drawing_agent_server.py
  wc -l drawing_agent_server.py                           # at least 350 fewer
  grep "^@app\.post(\"/upload-bom\")" drawing_agent_server.py    # no output
  grep "^@app\.post(\"/chat\")" drawing_agent_server.py          # no output
  grep "^@app\.get(\"/mcp/tools\")" drawing_agent_server.py      # no output
  grep "^@app\.post(\"/api/upload-bom\")" drawing_agent_server.py  # still present
  grep "^@app\.post(\"/api/pov/generate\")" drawing_agent_server.py # still present
  pytest tests/ -q --tb=short 2>&1 | tail -5             # same pass count

Commit message: p36a: remove bare alias routes and confirmed dead routes from drawing_agent_server.py

Branch: claude/explore-repo-Os53i (create fresh from main first with git fetch origin && git checkout -b claude/p36a origin/main)
Push when done.
```

---

## Prompt 2a — p36b: Extract terraform_generate (run after p36a merges)

```
Implement tasks/p36b-extract-terraform.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p36b origin/main

Then run the prerequisite check:
  python3.11 -m compileall drawing_agent_server.py agent/tools/terraform.py
  wc -l drawing_agent_server.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. In agent/tools/terraform.py, add an async function generate_terraform_bundle()
   with signature:
     async def generate_terraform_bundle(*, customer_id, customer_name, prompt, store, trace_id) -> dict

   Move into this function everything from the terraform_generate handler body:
   - read_context, get_new_notes, build_context_summary calls
   - Default prompt construction block
   - sub_agent_client.call_sub_agent() call
   - JSON parsing and file_aliases mapping
   - _terraform_fallback_files() fallback branch
   - save_terraform_bundle() persistence
   - record_agent_run() + write_context() context recording
   - Return dict construction
   The need_clarification early return becomes a dict return (not HTTPException).
   Add necessary imports (anyio, functools, json, etc.) at top of terraform.py.

2. In drawing_agent_server.py, replace the terraform_generate handler body with
   a 6-line delegate to generate_terraform_bundle(). Add the import at the top:
     from agent.tools.terraform import generate_terraform_bundle

Verify all acceptance criteria:
  python3.11 -m compileall drawing_agent_server.py agent/tools/terraform.py
  wc -l drawing_agent_server.py                    # at least 130 fewer than after p36a
  grep "generate_terraform_bundle" agent/tools/terraform.py  # matches
  pytest tests/ -q --tb=short 2>&1 | tail -5      # same pass count

Commit message: p36b: extract terraform_generate handler logic into agent/tools/terraform.py
Push to branch claude/p36b.
```

---

## Prompt 2b — p36c: Extract streaming chat (run after p36a merges, parallel with p36b)

```
Implement tasks/p36c-extract-chat-stream.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p36c origin/main

Then run the prerequisite check:
  python3.11 -m compileall drawing_agent_server.py
  wc -l drawing_agent_server.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. Create agent/chat_stream.py with an async generator function stream_chat_turn():
     async def stream_chat_turn(*, customer_id, customer_name, message, store, text_runner, a2a_base_url="")
       -> AsyncGenerator[str, None]
   
   Move into it from api_chat_stream:
   - asyncio.Queue setup and background task launching (_run_orchestrator_turn)
   - The while True polling/event loop
   - Event type dispatch (status, tool_call, tool_result, artifact, reply, error)
   - _persist_bom_xlsx_downloads, _build_artifact_manifest, _persist_chat_project_membership calls
   - Final reply event yield with artifacts
   Each yield should be a JSON-encoded string (one line per event).

2. In drawing_agent_server.py, replace api_chat_stream handler body (keep the
   @app.post decorator) with a ≤12-line delegate:
     return StreamingResponse(stream_chat_turn(...), media_type="application/x-ndjson")
   Add import: from agent.chat_stream import stream_chat_turn

Verify all acceptance criteria:
  python3.11 -m compileall drawing_agent_server.py agent/chat_stream.py
  wc -l drawing_agent_server.py                    # at least 170 fewer than after p36a
  ls agent/chat_stream.py                          # exists
  grep "stream_chat_turn" agent/chat_stream.py     # matches
  pytest tests/ -q --tb=short 2>&1 | tail -5      # same pass count

Commit message: p36c: extract streaming chat assembly into agent/chat_stream.py
Push to branch claude/p36c.
```

---

## Prompt 2c — p36d: Thin specialist handlers (run after p36a merges, parallel with p36b/p36c)

```
Implement tasks/p36d-thin-specialist-handlers.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p36d origin/main

Then run the prerequisite check:
  python3.11 -m compileall drawing_agent_server.py agent/tools/specialists.py
  wc -l drawing_agent_server.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. In agent/tools/specialists.py, add:
     def build_inference_runner(app_state, *, inference_config: dict)
   It returns getattr(app_state, "text_runner", None) if present, otherwise
   constructs and returns a runner closure that calls run_inference with the
   config dict keys: endpoint, model_id, compartment_id, max_tokens,
   temperature, top_p, top_k.

2. In drawing_agent_server.py, after the config constants block (~line 200-300),
   add _WRITING_INFERENCE_CONFIG dict mapping the WRITING_* constants.

3. In pov_generate, waf_generate, and jep_generate handlers, replace each
   "def _run_X(): def runner(prompt, system_message=''): ..." closure with:
     text_runner = build_inference_runner(app.state, inference_config=_WRITING_INFERENCE_CONFIG)
   then call generate_pov / generate_waf / generate_jep directly using anyio.to_thread.run_sync.
   Leave ALL jep_generate lifecycle/state-machine logic intact.

4. Add import at top of drawing_agent_server.py:
     from agent.tools.specialists import build_inference_runner

Verify all acceptance criteria:
  python3.11 -m compileall drawing_agent_server.py agent/tools/specialists.py
  wc -l drawing_agent_server.py                    # at least 50 fewer than after p36a
  grep "build_inference_runner" agent/tools/specialists.py  # matches
  grep -c "def runner(prompt, system_message" drawing_agent_server.py  # 0
  grep -c "build_inference_runner" drawing_agent_server.py  # at least 3
  pytest tests/ -q --tb=short 2>&1 | tail -5      # same pass count

Commit message: p36d: eliminate duplicated inference runner closures in POV/WAF/JEP handlers
Push to branch claude/p36d.
```

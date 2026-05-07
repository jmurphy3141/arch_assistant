# Task p36a: Delete Dead Routes from drawing_agent_server.py

## Goal

Remove ~24 provably dead route registrations and their handlers from
`drawing_agent_server.py`. A UI route audit confirmed all frontend calls use
the `/api/*` prefix exclusively — bare aliases are unreachable. Additional
routes are confirmed dead by explicit legacy naming or missing `await` bugs.

After this task, `drawing_agent_server.py` shrinks by approximately 400 lines
with zero behavior change to any active endpoint.

---

## Prerequisite Check

```bash
wc -l drawing_agent_server.py
grep -c "^@app\." drawing_agent_server.py
pytest tests/ -v --tb=short -q 2>&1 | tail -5
```

Record the line count and route count — acceptance criteria will verify reduction.

---

## Scope

**Only modify:**
- `drawing_agent_server.py`

**Do NOT touch any other file.**

---

## What to delete

### Category 1: Bare alias decorators — remove only the bare @app decorator line

These routes are defined with two decorators: one bare (e.g. `@app.post("/upload-bom")`)
and one with `/api/` prefix (e.g. `@app.post("/api/upload-bom")`). The UI only
calls the `/api/*` form. Remove only the bare decorator line — keep the `/api/*`
decorator and the handler function intact.

Pattern: for each pair below, delete the first decorator line only.

| Line | Remove this decorator |
|------|-----------------------|
| 1723 | `@app.post("/upload-to-bucket")` |
| 1761 | `@app.post("/upload-bom")` |
| 1906 | `@app.post("/clarify")` |
| 2066 | `@app.post("/refine")` |
| 2336 | `@app.post("/generate")` |
| 4350 | `@app.post("/notes/upload")` |
| 4377 | `@app.get("/notes/{customer_id}")` |
| 4394 | `@app.post("/pov/generate")` |
| 4447 | `@app.get("/pov/{customer_id}/latest")` |
| 4460 | `@app.get("/pov/{customer_id}/versions")` |
| 4473 | `@app.post("/jep/generate")` |
| 4545 | `@app.get("/jep/{customer_id}/latest")` |
| 4567 | `@app.get("/jep/{customer_id}/versions")` |
| 4799 | `@app.post("/waf/generate")` |
| 4856 | `@app.get("/waf/{customer_id}/latest")` |
| 4874 | `@app.get("/waf/{customer_id}/versions")` |
| 4887 | `@app.post("/terraform/generate")` |
| 5045 | `@app.get("/terraform/{customer_id}/latest")` |
| 5071 | `@app.get("/terraform/{customer_id}/versions")` |
| 5087 | `@app.get("/context/{customer_id}")` |

**After removing each bare decorator line, verify the `/api/*` decorator
immediately below it is still present and the handler function is intact.**

---

### Category 2: Delete entirely — handler + all decorators

These routes have no `/api/*` counterpart and no UI caller. Delete the decorator
AND the entire handler function body.

#### `POST /chat` (line 2410, ~10 lines)

```python
@app.post("/chat")
async def chat(req: ChatRequest, _user: dict = Depends(require_user)):
    ...
```

Delete from `@app.post("/chat")` through the end of the `chat` function body
(before the next `@app.get` decorator). This route calls `call_llm` without
`await` — it is broken and duplicates `/api/chat`.

#### `GET /mcp/tools` (line 2695, ~56 lines)

```python
@app.get("/mcp/tools")
async def mcp_tools():
    ...
```

Delete from `@app.get("/mcp/tools")` through the end of `mcp_tools`.
This returns a hardcoded static tool schema; not called by UI or any known
external client.

#### `GET /mcp/tools/get_oci_catalogue` (line 2751, ~12 lines)

```python
@app.get("/mcp/tools/get_oci_catalogue")
async def get_catalogue():
    ...
```

Delete from `@app.get("/mcp/tools/get_oci_catalogue")` through end of
`get_catalogue` handler.

#### `GET /.well-known/agent-card-legacy.json` (line 2948, ~22 lines)

```python
@app.get("/.well-known/agent-card-legacy.json")
async def agent_card_legacy():
    ...
```

Delete from `@app.get("/.well-known/agent-card-legacy.json")` through end of
`agent_card_legacy` handler.

---

### Category 3: Do NOT delete in this task

Leave these routes untouched — they need separate caller investigation:
- `POST /message:send` (line 2970)
- `GET /tasks/{task_id}` (line 3102)
- `POST /tasks/{task_id}:cancel` (line 3111)

Also leave untouched:
- `GET /health` (bare, active — UI calls it bare)
- `GET /.well-known/agent.json`
- `GET /.well-known/agent-card.json`

---

## After deletion: cleanup check

```bash
python3.11 -m compileall drawing_agent_server.py
```

Must exit 0. If any `NameError` or `undefined name` appears, a deleted handler
referenced a helper that is now unused — check if the helper is referenced
elsewhere before considering removal. For this task, leave all helpers intact
even if they become unused; helper cleanup is p36b–p36d.

---

## Acceptance Criteria

1. `python3.11 -m compileall drawing_agent_server.py` exits 0
2. `wc -l drawing_agent_server.py` — at least 350 lines fewer than before
3. `grep "^@app\.post(\"/upload-bom\")" drawing_agent_server.py` — no output
4. `grep "^@app\.post(\"/chat\")" drawing_agent_server.py` — no output
5. `grep "^@app\.get(\"/mcp/tools\")" drawing_agent_server.py` — no output
6. `grep "^@app\.get(\"/.well-known/agent-card-legacy" drawing_agent_server.py` — no output
7. `grep "^@app\.post(\"/api/upload-bom\")" drawing_agent_server.py` — still present
8. `grep "^@app\.post(\"/api/pov/generate\")" drawing_agent_server.py` — still present
9. `grep "^@app\.post(\"/api/terraform/generate\")" drawing_agent_server.py` — still present
10. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Do NOT Do

- Do not delete the `/api/*` form of any route — only the bare alias decorator line
- Do not delete `POST /message:send`, `GET /tasks/*` — those are deferred
- Do not delete `GET /health` — the UI calls this bare
- Do not delete `GET /.well-known/agent.json` or `agent-card.json`
- Do not modify any handler function logic — only remove decorators and dead handlers
- Do not remove any helper function — only route handlers

---

## Commit Message

```
p36a: remove bare alias routes and confirmed dead routes from drawing_agent_server.py
```

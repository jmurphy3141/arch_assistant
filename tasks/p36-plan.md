# Phase 3.6 Plan — drawing_agent_server.py Reduction

## Context

After Phase 2–3.5, `archie_loop.py` is down from 6,441 to ~4,100 lines and all
tool dispatch runs through SkillForge. `drawing_agent_server.py` (5,123 lines)
is the next major target. It acts as router, controller, service layer, workflow
engine, artifact policy layer, and compatibility gateway simultaneously.

A UI route audit confirmed that **all UI calls use `/api/*` prefix**. Bare
routes (`/upload-bom`, `/clarify`, `/pov/generate`, etc.) have zero callers.

---

## Guiding Principles

Same Strangler Fig pattern as Phase 2/3:
1. Delete dead code first (lowest risk, immediate payoff)
2. Extract business logic out of handlers (medium risk, tests required)
3. Thin handlers delegate to extracted modules (medium risk)
4. Remove legacy API compatibility routes (high risk, needs per-route caller audit)

**Never remove a route without proving it has no callers.**
**Never change external behavior — only internal structure.**

---

## Task Index

| Task | Description | Risk | Lines saved | Depends on |
|------|-------------|------|-------------|-----------|
| p36a | Delete bare alias routes + confirmed dead routes | Low | ~400 | none |
| p36b | Extract `terraform_generate` handler logic | Medium | ~140 | p36a |
| p36c | Extract streaming chat assembly (`api_chat_stream`) | Medium | ~180 | p36a |
| p36d | Thin POV/JEP/WAF generate handlers | Medium | ~60 | p36a |
| p36e | Extract `refine_diagram` (266-line handler) | High | ~240 | p36b,p36c,p36d |
| p36f | Consolidate diagram pipeline (upload_bom, clarify, generate) | High | ~300 | p36e |

**Target after p36a–p36d:** ~800 lines removed. Server drops from 5,123 to ~4,300.
**Target after p36e–p36f:** Additional ~540 lines. Server reaches ~3,760.

---

## Routes confirmed dead (UI audit)

### Bare aliases — UI only calls /api/* prefix
These routes are registered twice: once as `/foo` and once as `/api/foo`.
The UI only calls `/api/foo`. The bare form is dead.

- `POST /upload-to-bucket` (line 1723) — bare alias of `/api/upload-to-bucket`
- `POST /upload-bom` (line 1761) — bare alias of `/api/upload-bom`
- `POST /clarify` (line 1906) — bare alias of `/api/clarify`
- `POST /refine` (line 2066) — bare alias of `/api/refine`
- `POST /generate` (line 2336) — bare alias of `/api/generate`
- `POST /notes/upload` (line 4350) — bare alias of `/api/notes/upload`
- `GET /notes/{customer_id}` (line 4377) — bare alias of `/api/notes/{customer_id}`
- `POST /pov/generate` (line 4394) — bare alias of `/api/pov/generate`
- `GET /pov/{customer_id}/latest` (line 4447) — bare alias of `/api/pov/{customer_id}/latest`
- `GET /pov/{customer_id}/versions` (line 4460) — bare alias of `/api/pov/{customer_id}/versions`
- `POST /jep/generate` (line 4473) — bare alias of `/api/jep/generate`
- `GET /jep/{customer_id}/latest` (line 4545) — bare alias of `/api/jep/{customer_id}/latest`
- `GET /jep/{customer_id}/versions` (line 4567) — bare alias of `/api/jep/{customer_id}/versions`
- `POST /waf/generate` (line 4799) — bare alias of `/api/waf/generate`
- `GET /waf/{customer_id}/latest` (line 4856) — bare alias of `/api/waf/{customer_id}/latest`
- `GET /waf/{customer_id}/versions` (line 4874) — bare alias of `/api/waf/{customer_id}/versions`
- `POST /terraform/generate` (line 4887) — bare alias of `/api/terraform/generate`
- `GET /terraform/{customer_id}/latest` (line 5045) — bare alias
- `GET /terraform/{customer_id}/versions` (line 5071) — bare alias
- `GET /context/{customer_id}` (line 5087) — bare alias of `/api/context/{customer_id}`

### Buggy dead routes
- `POST /chat` (line 2410) — missing `await` on `call_llm`; duplicates `/api/chat`;
  not called by UI; never worked correctly.

### Confirmed no-caller routes
- `GET /mcp/tools` (line 2695) — static hardcoded tool schema; not called by UI or A2A
- `GET /mcp/tools/get_oci_catalogue` (line 2751) — not called anywhere
- `GET /.well-known/agent-card-legacy.json` (line 2948) — explicitly named legacy

### Defer to separate investigation (do NOT delete in p36a)
- `POST /message:send` (line 2970) — A2A v1 JSON-RPC; may have external callers
- `GET /tasks/{task_id}` (line 3102) — A2A v1; investigate before removing
- `POST /tasks/{task_id}:cancel` (line 3111) — A2A v1; investigate before removing

---

## Routes confirmed active (UI calls these)
- `GET /health` — bare, active (UI calls it bare)
- All `/api/*` routes
- `GET /.well-known/agent.json` — A2A discovery
- `GET /.well-known/agent-card.json` — A2A discovery (keep despite "legacy alias" comment)
- `POST /api/a2a/task` — UI calls this (client.ts line 400)

---

## Success Criteria

After p36a–p36d:
- `wc -l drawing_agent_server.py` ≤ 4,350 (down from 5,123)
- All existing tests pass
- UI smoke test: `/api/chat`, `/api/pov/generate`, `/api/jep/generate`,
  `/api/waf/generate`, `/api/terraform/generate` all respond correctly
- `grep "^@app.post\(\"/upload-bom\"\)\|^@app.post\(\"/chat\"\)\|^@app.get\(\"/mcp/tools\"\)" drawing_agent_server.py` — no output

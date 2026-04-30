# Task: BOM sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md, p1-sub-agent-diagram.md (pattern established)

## Goal
Extract BOM generation into an independent A2A service backed by the existing
`bom_service.py`. The BOM agent handles pricing, validation, and XLSX generation.

## Context
The existing BOM logic lives in:
- `agent/bom_service.py` — pricing cache, chat interface, validation, repair loop, XLSX generation
- `agent/bom_stub.py` — offline stub for tests

The sub-agent wraps `bom_service.py`. Do not move or rewrite `bom_service.py`.

## Files to create

### `sub_agents/bom/__init__.py`
Empty.

### `sub_agents/bom/system_prompt.md`
The BOM agent's identity and instructions. Source material is
`gstack_skills/oci_bom_expert/SKILL.md`. Write it as the agent's own system
prompt: who it is, what it produces, OCI pricing rules it applies, validation
rules it enforces, what it returns.

### `sub_agents/bom/config.yaml`
```yaml
name: bom
port: 8083
llm:
  model_id: ""
  max_tokens: 4000
  temperature: 0.2
```

### `sub_agents/bom/server.py`
Agent card:
```json
{
  "name": "bom",
  "description": "Produces a priced OCI Bill of Materials from workload inputs.",
  "inputs": {
    "required": ["task"],
    "optional": ["region", "engagement_context", "trace_id"]
  },
  "output": "Structured BOM JSON with line items and monthly cost total",
  "llm_model_id": "<from config>"
}
```

The handler:
1. Gets the shared BOM service: `get_shared_bom_service()`
2. Calls `service.chat(message=req.task, conversation=[], trace_id=req.trace_id)`
3. If the service returns a validation error or repair failure, returns
   `A2AResponse(status="needs_input", result=error_detail)`
4. On success returns `A2AResponse(result=bom_json, status="ok", trace=service_trace)`

### `sub_agents/bom/README.md`
```markdown
# BOM Sub-Agent

Produces priced OCI Bills of Materials.

## Run
python3.11 -m uvicorn sub_agents.bom.server:app --port 8083

## Card
GET http://localhost:8083/a2a/card

## Call
POST http://localhost:8083/a2a
{"task": "Size a 3-tier web application: 4 OCPUs compute, 64GB RAM, 10TB block storage, us-chicago-1"}
```

## Files to NOT touch
- `agent/bom_service.py` — do not modify
- `agent/bom_stub.py` — do not modify
- `drawing_agent_server.py`
- Any existing test file

## What to do
1. Create the files listed above.
2. In `server.py` import from the existing service:
   ```python
   from agent.bom_service import get_shared_bom_service
   ```
3. Implement the handler.
4. The BOM service requires its pricing cache to be warmed. Add a FastAPI
   `lifespan` handler that calls `service.refresh_data()` on startup
   if `service.health()["ready"]` is False.

## Acceptance criteria
- `python3.11 -m uvicorn sub_agents.bom.server:app --port 8083 &`
  then `curl http://localhost:8083/health` returns `{"status":"ok","agent":"bom"}`
- `curl http://localhost:8083/a2a/card` returns valid card with `name: "bom"`
- `python3.11 -m compileall sub_agents/bom/` exits 0
- `pytest tests/test_bom_service.py tests/test_bom_api.py -v -m "not live"` passes

# Task: POV sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md

## Goal
Extract Point-of-View document generation into an independent A2A service.

## Context
Existing logic: `agent/pov_agent.py` — `generate_pov(customer_id, store, text_runner, feedback)`.
The sub-agent wraps this function. Do not modify `pov_agent.py`.

## Files to create

### `sub_agents/pov/__init__.py`
Empty.

### `sub_agents/pov/system_prompt.md`
Source material: `gstack_skills/oci_customer_pov_writer/SKILL.md`.
Write as the agent's own system prompt: its role, what a POV document contains,
tone, structure, what inputs it needs.

### `sub_agents/pov/config.yaml`
```yaml
name: pov
port: 8084
llm:
  model_id: ""
  max_tokens: 4000
  temperature: 0.7
```

### `sub_agents/pov/server.py`
Agent card:
```json
{
  "name": "pov",
  "description": "Writes an OCI Point-of-View document for a customer engagement.",
  "inputs": {
    "required": ["task"],
    "optional": ["customer_name", "engagement_context", "prior_version", "trace_id"]
  },
  "output": "POV document in Markdown",
  "llm_model_id": "<from config>"
}
```

The handler:
1. Uses `req.task` as the combined brief (Archie constructed it from engagement context)
2. Calls `agent/llm_inference_client.py` `run_inference()` with the system prompt
   from `system_prompt.md` and the task as user content
3. If `req.engagement_context.get("prior_version")` is present, instructs the LLM
   to treat it as the prior draft to update
4. Returns `A2AResponse(result=markdown_text, status="ok")`

Do not call `pov_agent.generate_pov()` directly — the sub-agent uses its own
`system_prompt.md` and LLM call, which is the point of independence.

### `sub_agents/pov/README.md`
```markdown
# POV Sub-Agent

Writes OCI Point-of-View documents.

## Run
python3.11 -m uvicorn sub_agents.pov.server:app --port 8084

## Card
GET http://localhost:8084/a2a/card

## Call
POST http://localhost:8084/a2a
{"task": "Write a POV for Acme Corp migrating their 3-tier web app to OCI Chicago region..."}
```

## Files to NOT touch
- `agent/pov_agent.py`
- Any existing test file
- `drawing_agent_server.py`

## Acceptance criteria
- `curl http://localhost:8084/health` returns `{"status":"ok","agent":"pov"}`
- `curl http://localhost:8084/a2a/card` returns valid card
- `python3.11 -m compileall sub_agents/pov/` exits 0
- `pytest tests/ -v -m "not live"` passes (no regressions)

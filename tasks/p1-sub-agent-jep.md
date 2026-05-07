# Task: JEP sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md

## Goal
Extract JEP (Joint Engagement Plan) document generation into an independent A2A service.

## Context
Existing logic:
- `agent/jep_agent.py` — `generate_jep()`, `kickoff_jep()`
- `agent/jep_lifecycle.py` — JEP state machine (draft → review → approved)

The sub-agent wraps the generation step. `jep_lifecycle.py` state management
stays in the main server for now — do not move it in this task.

## Files to create

### `sub_agents/jep/__init__.py`
Empty.

### `sub_agents/jep/system_prompt.md`
Source material: `gstack_skills/oci_jep_writer/SKILL.md`.
Write as the agent's own system prompt: its role, JEP document structure,
what sections a JEP must contain, what inputs it needs.

### `sub_agents/jep/config.yaml`
```yaml
name: jep
port: 8085
llm:
  model_id: ""
  max_tokens: 4000
  temperature: 0.7
```

### `sub_agents/jep/server.py`
Agent card:
```json
{
  "name": "jep",
  "description": "Writes an OCI Joint Engagement Plan document.",
  "inputs": {
    "required": ["task"],
    "optional": ["customer_name", "engagement_context", "prior_version", "feedback", "trace_id"]
  },
  "output": "JEP document in Markdown",
  "llm_model_id": "<from config>"
}
```

Handler:
1. Uses `req.task` as the generation brief
2. If `req.engagement_context.get("feedback")` is present, treats the call
   as a revision request
3. If `req.engagement_context.get("prior_version")` is present, includes it
   as the prior draft to update
4. Calls `run_inference()` with the system prompt + task
5. Returns `A2AResponse(result=markdown_text, status="ok")`

### `sub_agents/jep/README.md`
```markdown
# JEP Sub-Agent

Writes OCI Joint Engagement Plan documents.

## Run
python3.11 -m uvicorn sub_agents.jep.server:app --port 8085

## Card
GET http://localhost:8085/a2a/card

## Call
POST http://localhost:8085/a2a
{"task": "Write a JEP for Acme Corp OCI migration engagement..."}
```

## Files to NOT touch
- `agent/jep_agent.py`
- `agent/jep_lifecycle.py`
- Any existing test file
- `drawing_agent_server.py`

## Acceptance criteria
- `curl http://localhost:8085/health` returns `{"status":"ok","agent":"jep"}`
- `curl http://localhost:8085/a2a/card` returns valid card
- `python3.11 -m compileall sub_agents/jep/` exits 0
- `pytest tests/test_jep_lifecycle.py -v` passes (no regressions)

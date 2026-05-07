# Task: WAF sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md

## Goal
Extract Well-Architected Framework review into an independent A2A service.

## Context
Existing logic: `agent/waf_agent.py` — `generate_waf(customer_id, store, text_runner, feedback)`.
Do not modify `waf_agent.py`.

## Files to create

### `sub_agents/waf/__init__.py`
Empty.

### `sub_agents/waf/system_prompt.md`
Source material: `gstack_skills/oci_waf_reviewer/SKILL.md`.
Write as the agent's own system prompt: its role as an OCI WAF reviewer,
the six WAF pillars it evaluates, what a WAF review output contains.

### `sub_agents/waf/config.yaml`
```yaml
name: waf
port: 8086
llm:
  model_id: ""
  max_tokens: 4000
  temperature: 0.5
```

### `sub_agents/waf/server.py`
Agent card:
```json
{
  "name": "waf",
  "description": "Performs an OCI Well-Architected Framework review of an architecture.",
  "inputs": {
    "required": ["task"],
    "optional": ["architecture_summary", "diagram_context", "engagement_context", "feedback", "trace_id"]
  },
  "output": "WAF review in Markdown covering the six OCI WAF pillars",
  "llm_model_id": "<from config>"
}
```

Handler:
1. Uses `req.task` as the review brief
2. Includes any `architecture_summary` or `diagram_context` from
   `req.engagement_context` in the prompt
3. Returns `A2AResponse(result=markdown_review, status="ok")`

### `sub_agents/waf/README.md`
```markdown
# WAF Sub-Agent

Performs OCI Well-Architected Framework reviews.

## Run
python3.11 -m uvicorn sub_agents.waf.server:app --port 8086

## Card
GET http://localhost:8086/a2a/card

## Call
POST http://localhost:8086/a2a
{"task": "Review this OCI architecture for WAF compliance...",
 "engagement_context": {"architecture_summary": "..."}}
```

## Files to NOT touch
- `agent/waf_agent.py`
- `agent/diagram_waf_orchestrator.py`
- Any existing test file
- `drawing_agent_server.py`

## Acceptance criteria
- `curl http://localhost:8086/health` returns `{"status":"ok","agent":"waf"}`
- `curl http://localhost:8086/a2a/card` returns valid card
- `python3.11 -m compileall sub_agents/waf/` exits 0
- `pytest tests/test_waf_agent.py -v -m "not live"` passes

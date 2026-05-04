# Task: Diagram sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md

## Goal
Extract the diagram generation pipeline into an independent A2A service.
This is the first sub-agent to migrate because the diagram path already has
an A2A shape inside the existing server. Use it to validate the pattern before
migrating more complex agents.

## Context
The existing pipeline lives in:
- `agent/bom_parser.py` — parses BOM text or XLSX → ServiceItem list + LLM prompt
- `agent/intent_compiler.py` — validates LLM layout output
- `agent/layout_engine.py` — spec → x,y positions
- `agent/drawio_generator.py` — positions → draw.io XML
- `agent/llm_inference_client.py` — calls OCI GenAI inference
- `agent/reference_architecture.py` — selects reference patterns
- `agent/diagram_waf_orchestrator.py` — the current entry point used by the server

The sub-agent wraps this pipeline. The existing pipeline files in `agent/` are
NOT moved — the sub-agent imports them.

## Files to create

### `sub_agents/diagram/__init__.py`
Empty.

### `sub_agents/diagram/system_prompt.md`
The diagram agent's own identity and instructions. Write this fresh — do not
copy from `gstack_skills/diagram_for_oci/SKILL.md` verbatim but use it as
source material. The system prompt should describe:
- The agent's job: take a workload description and produce a draw.io XML diagram
- OCI layout rules (subnets, gateways, flat XML structure)
- What it returns

### `sub_agents/diagram/config.yaml`
```yaml
name: diagram
port: 8082
llm:
  model_id: ""          # inherits from main config if empty
  max_tokens: 4000
  temperature: 0.0
```

### `sub_agents/diagram/server.py`
FastAPI app using `make_agent_app` from `sub_agents.base`.

Agent card:
```json
{
  "name": "diagram",
  "description": "Generates an OCI architecture draw.io diagram from a workload description.",
  "inputs": {
    "required": ["task"],
    "optional": ["diagram_name", "customer_id", "trace_id"]
  },
  "output": "draw.io XML string",
  "llm_model_id": "<from config>"
}
```

The handler:
1. Reads `req.task` as the workload/BOM description
2. Calls the existing pipeline:
   `bom_parser → llm inference → intent_compiler → layout_engine → drawio_generator`
3. Returns `A2AResponse(result=drawio_xml, status="ok", trace={...})`

Use `agent/llm_inference_client.py` for the LLM call (do not create a new client).
Read LLM config from `sub_agents/diagram/config.yaml` at startup, falling back to
`config.yaml` `inference:` block for the model_id if the sub-agent config is empty.

### `sub_agents/diagram/README.md`
```markdown
# Diagram Sub-Agent

Generates OCI architecture draw.io diagrams.

## Run
python3.11 -m uvicorn sub_agents.diagram.server:app --port 8082

## Card
GET http://localhost:8082/a2a/card

## Call
POST http://localhost:8082/a2a
{"task": "3-tier web app with load balancer, 2 app servers, ATP database"}
```

## Files to NOT touch
- Any file in `agent/` — the pipeline stays where it is
- `drawing_agent_server.py` — Archie still calls sub-agents via the client (p1-archie-client.md)
- `PLAN.md`, `AGENTS.md`, `CLAUDE.md`
- Any existing test file

## What to do
1. Create the directory and files listed above.
2. In `server.py`, import the pipeline modules from `agent/`:
   ```python
   from agent.bom_parser import freeform_arch_text_to_llm_input
   from agent.intent_compiler import compile_intent
   from agent.layout_engine import spec_to_draw_dict
   from agent.drawio_generator import generate_drawio
   from agent.llm_inference_client import run_inference
   ```
3. Implement the handler to run the pipeline and return draw.io XML.
4. Handle the case where the LLM returns a clarification request
   (`status == "need_clarification"`) by returning
   `A2AResponse(status="needs_input", result=questions_json)`.

## Acceptance criteria
- `python3.11 -m uvicorn sub_agents.diagram.server:app --port 8082 &`
  then `curl http://localhost:8082/health` returns `{"status":"ok","agent":"diagram"}`
- `curl http://localhost:8082/a2a/card` returns a valid card with `name: "diagram"`
- `python3.11 -m compileall sub_agents/diagram/` exits 0
- `pytest tests/ -v -m "not live"` still passes (no regressions)

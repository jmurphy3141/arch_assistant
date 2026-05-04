# Task: Terraform sub-agent
Phase: 1
Status: todo
Depends on: p1-a2a-base.md

## Goal
Extract Terraform generation into an independent A2A service configured to use
a code-optimised LLM. This agent generates production-ready OCI Terraform modules.

## Context
Existing logic: `agent/graphs/terraform_graph.py` — wraps orchestrator Terraform path.
Source material for system prompt: `gstack_skills/terraform_for_oci/SKILL.md`.

Do not modify `terraform_graph.py`. The sub-agent is a clean implementation
using its own system prompt and LLM config.

## Files to create

### `sub_agents/terraform/__init__.py`
Empty.

### `sub_agents/terraform/system_prompt.md`
Source material: `gstack_skills/terraform_for_oci/SKILL.md`.
Write as the agent's own system prompt: its role as an OCI Terraform specialist,
module structure it produces, OCI provider conventions, what it returns.

The system prompt must emphasise:
- Return only valid HCL, no markdown fences around the final output
- Use OCI Terraform provider v5+
- Produce four files: `main.tf`, `variables.tf`, `outputs.tf`, `README.md`

### `sub_agents/terraform/config.yaml`
```yaml
name: terraform
port: 8087
llm:
  model_id: ""    # set to code-optimised model OCID when available; falls back to default
  max_tokens: 6000
  temperature: 0.2
```

### `sub_agents/terraform/server.py`
Agent card:
```json
{
  "name": "terraform",
  "description": "Generates OCI Terraform modules from an architecture description.",
  "inputs": {
    "required": ["task"],
    "optional": ["architecture_summary", "region", "compartment_id", "engagement_context", "trace_id"]
  },
  "output": "JSON object with keys: main_tf, variables_tf, outputs_tf, readme_md",
  "llm_model_id": "<from config>"
}
```

Handler:
1. Uses `req.task` as the generation brief
2. Extracts `region` and `compartment_id` from `req.engagement_context` if present
3. Calls `run_inference()` with the system prompt + task
4. Parses the LLM response into the four-file structure
5. Returns:
   ```json
   {
     "result": "{\"main_tf\": \"...\", \"variables_tf\": \"...\", \"outputs_tf\": \"...\", \"readme_md\": \"...\"}",
     "status": "ok"
   }
   ```
   (`result` is a JSON string so Archie can parse or pass through as needed)

### `sub_agents/terraform/README.md`
```markdown
# Terraform Sub-Agent

Generates OCI Terraform modules.

## Run
python3.11 -m uvicorn sub_agents.terraform.server:app --port 8087

## LLM
Set `llm.model_id` in config.yaml to a code-optimised model OCID for best results.
Defaults to the main inference model if empty.

## Card
GET http://localhost:8087/a2a/card

## Call
POST http://localhost:8087/a2a
{"task": "Generate Terraform for a 3-tier web app in us-chicago-1...",
 "engagement_context": {"region": "us-chicago-1", "compartment_id": "ocid1..."}}
```

## Files to NOT touch
- `agent/graphs/terraform_graph.py`
- Any existing test file
- `drawing_agent_server.py`

## What to do
1. Create files listed above.
2. Read the LLM model_id from `sub_agents/terraform/config.yaml` at startup.
   If empty, fall back to the `inference.model_id` in the root `config.yaml`.
3. This fallback pattern should be a simple helper in `sub_agents/terraform/server.py` —
   do not create a shared utility for it unless another sub-agent needs the same.

## Acceptance criteria
- `curl http://localhost:8087/health` returns `{"status":"ok","agent":"terraform"}`
- `curl http://localhost:8087/a2a/card` returns valid card with `name: "terraform"`
- `python3.11 -m compileall sub_agents/terraform/` exits 0
- `pytest tests/test_terraform_graph.py tests/test_terraform_api.py -v -m "not live"` passes

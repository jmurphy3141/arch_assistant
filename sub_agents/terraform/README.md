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

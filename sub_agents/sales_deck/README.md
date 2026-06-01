# sales_deck Sub-Agent

OCI customer-facing sales deck specialist.

Produces structured JSON slide specifications from customer engagement context,
POV artifacts, BOM artifacts, and diagram references.

Port: 8089 (see config.yaml)
System prompt: system_prompt.md
Pattern: A2A via sub_agent_client.call_sub_agent("sales_deck", ...)
Output: JSON slide spec saved as deck/customer-id/vN.json
Renderer: p54d will add python-pptx rendering from this JSON spec

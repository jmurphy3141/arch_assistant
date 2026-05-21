# tech_research Sub-Agent

OCI infrastructure technology research and evaluation specialist.

Evaluates architecture options, maps workloads to OCI services, and produces
a structured assessment with sizing hints for BOM and Diagram generation.

Port: 8087 (see config.yaml)
System prompt: system_prompt.md
Pattern: A2A via `sub_agent_client.call_sub_agent("tech_research", ...)`

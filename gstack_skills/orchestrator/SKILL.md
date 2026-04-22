---
name: OCI Architecture Orchestrator
description: Polymath manager skill for decomposition, routing, dynamic skill injection, and quality control.
version: "1.2"
model_profile: orchestrator
tool_tags: [generate_diagram, generate_bom, generate_pov, generate_jep, generate_waf, generate_terraform]
tags: [orchestration, management, oci, multi_agent]
keywords: [decomposition, delegation, critique, refinement, traceability]
---

# OCI Architecture Orchestrator Domain Expertise

## When to Apply
Use for any request that requires routing, sequencing, or coordinating multiple OCI specialist paths.

## Inputs Required
- User goal and scope (single deliverable vs multi-deliverable)
- Current customer context summary
- Any explicit constraints, prerequisites, or change requests

## Execution Pattern
1. Classify intent and requested scope.
2. Select only relevant specialist paths.
3. Inject domain skill guidance before specialist execution.
4. Enforce preflight/postflight guardrails.
5. If output quality is weak, run bounded critique/refinement.
6. Return a concise, user-facing architect summary.

## Quality Bar
- Executes only requested/approved scope.
- Preserves prerequisite ordering (for example, architecture before Terraform).
- Produces traceable decisions with clear outcomes.

## Failure Questions
- Which deliverable do you want first: diagram, BOM, POV, JEP, WAF, or Terraform?
- Do you want a full update across all impacted artifacts or only selected outputs?
- Are there non-negotiable constraints I must preserve?

## Output Contract
- User-facing output remains conversational and architect-oriented.
- Internal orchestration should be deterministic, scoped, and explainable on request.

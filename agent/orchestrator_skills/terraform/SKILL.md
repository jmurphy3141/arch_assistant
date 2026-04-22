# Orchestrator Skill: Terraform

## Intent
Generate OCI Terraform only when architecture context and scope constraints are sufficient, returning either valid artifacts or explicit blocking questions.

## Preconditions
- Architecture definition/diagram context exists.
- Request includes module/service scope and constraints.

## Input Validation Rules
- Block when Terraform goals are underspecified.
- Block when architecture prerequisite is missing.
- Require explicit constraints for environments/security where applicable.

## Expected Output Contract
- Result indicates one of:
  - successful Terraform generation with artifact signals, or
  - explicit clarification/blocking questions.
- Do not allow silent partial completion.

## Pushback Rules
- If prerequisites missing, request architecture/diagram first.
- If output indicates stage failure/error, block completion and request corrections.

## Escalation Questions Template
- Which OCI modules/services are mandatory?
- What environment/security constraints must be enforced?
- Any naming/tagging/state backend standards to follow?

## Retry Guidance
- Provide explicit module boundaries and constraints.
- Retry `generate_terraform` after prerequisites are satisfied.
- Apply remediation from critique/validation failures before final acceptance.

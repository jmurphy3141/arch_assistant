# Orchestrator Skill: Terraform

## Intent
Ensure Terraform generation runs with clear constraints and that blocked specialist outcomes produce actionable pushback.

## Preconditions
- Customer context is identified.
- Request includes module scope/constraints or explicit goals.

## Input Validation Rules
- Block when Terraform request is underspecified.
- Require enough detail to determine OCI services, boundaries, or constraints.

## Expected Output Contract
- Tool result indicates completion or clear clarification questions.
- On completion, output is coherent and stage outcome is not failed.

## Pushback Rules
- Block and ask for missing scope details when input is vague.
- Block completion when stage result signals failure/error.

## Escalation Questions Template
- Which OCI services/modules should be generated?
- What environment/security constraints must be enforced?

## Retry Guidance
- Provide explicit module/service requirements and constraints.
- Retry `generate_terraform` with clarified scope.

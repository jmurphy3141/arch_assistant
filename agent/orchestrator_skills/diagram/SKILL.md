# Orchestrator Skill: Diagram

## Intent
Enforce that diagram generation only runs with sufficient BOM/resource input and that the result is verifiable.

## Preconditions
- Customer context is identified.
- Diagram request includes `bom_text` or explicit BOM upload context.

## Input Validation Rules
- Block when no BOM/resource details are present in tool args or request context.
- Require actionable OCI resource intent, not empty placeholders.

## Expected Output Contract
- Result summary states diagram generation started or completed.
- Completed result should include a persisted artifact key when available.

## Pushback Rules
- If preconditions fail, block and ask for BOM/resource details.
- If output contract fails, block completion and request retry with explicit inputs.

## Escalation Questions Template
- Which OCI resources and connectivity should appear in the diagram?
- Do you have BOM content to upload or paste as `bom_text`?

## Retry Guidance
- Provide BOM/resource details and rerun `generate_diagram`.
- If async execution starts, poll completion before reporting success.

# Orchestrator Skill: Diagram

## Intent
Generate or update OCI architecture diagrams with explicit topology intent, while preventing unsupported or input-empty diagram calls.

## Preconditions
- Customer context is identified.
- Diagram request includes either:
  - BOM/resource context (`bom_text`), or
  - Explicit architecture update/change details.

## Input Validation Rules
- Block when no architecture intent is present.
- Block when request is purely ambiguous (for example: "make a diagram" with no workload/network context).
- Allow iterative updates when change intent is explicit.

## Expected Output Contract
- Result must indicate accepted diagram progression:
  - started (async accepted), or
  - completed (artifact key available).
- Summary must not claim completion without verifiable signal.

## Pushback Rules
- If preconditions fail, ask for concrete OCI resource and connectivity intent.
- If output cannot be verified, block completion and request retry with explicit topology inputs.

## Escalation Questions Template
- Which OCI services and traffic paths must be represented?
- Which components are internet-facing vs private?
- Any HA/DR target (single AD, multi-AD, multi-region)?

## Retry Guidance
- Provide BOM/resource context and retry `generate_diagram`.
- For update requests, include only the specific change deltas to apply.
- If async, poll completion before announcing final success.

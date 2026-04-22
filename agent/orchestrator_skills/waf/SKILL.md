# Orchestrator Skill: WAF

## Intent
Run OCI Well-Architected reviews only when architecture context exists, and return persisted, actionable findings.

## Preconditions
- Diagram/architecture context exists.
- Customer context is identified.

## Input Validation Rules
- Block when no architecture/diagram context exists.
- Allow focused review feedback for iterative reassessment.

## Expected Output Contract
- Result summary indicates WAF review saved/updated.
- Persisted artifact key exists.
- Findings should be actionable and OCI-specific.

## Pushback Rules
- If architecture prerequisite is missing, require diagram generation first.
- If persistence/output contract fails, block completion.

## Escalation Questions Template
- Should review prioritize security, reliability, or cost first?
- Is there a specific environment slice to assess?
- Do you want topology-only or full narrative WAF review?

## Retry Guidance
- Generate/refresh diagram first when missing.
- Retry `generate_waf` after prerequisite context is available.
- Re-run after applying recommended topology remediations.

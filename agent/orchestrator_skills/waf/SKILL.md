# Orchestrator Skill: WAF

## Intent
Enforce that WAF review is run only when architecture context exists and output is persisted.

## Preconditions
- A diagram context exists for the customer.
- Customer context is identified.

## Input Validation Rules
- Block if no diagram context is present.
- No additional required args for baseline WAF execution.

## Expected Output Contract
- Result summary indicates WAF review was saved.
- Artifact key exists for the WAF result.

## Pushback Rules
- Block and request diagram generation when diagram context is absent.
- Block completion if output is not persisted.

## Escalation Questions Template
- Do you want to generate or refresh the architecture diagram first?
- Should WAF review focus on a specific environment or workload slice?

## Retry Guidance
- Run `generate_diagram` first when needed.
- Retry `generate_waf` after diagram context is available.

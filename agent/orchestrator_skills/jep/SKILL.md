# Orchestrator Skill: JEP

## Intent
Generate implementation-grade JEP outputs with explicit scope, milestones, ownership, and measurable success criteria.

## Preconditions
- Customer context exists.
- Notes/context summary is available.

## Input Validation Rules
- Block when notes/context is missing.
- Accept optional revision feedback.
- Require scope clarity when user asks for execution planning.

## Expected Output Contract
- Result summary indicates JEP saved/updated.
- Persisted artifact key exists for latest JEP output.

## Pushback Rules
- Request notes/context before generation when absent.
- Block completion if persistence/output contract is unmet.

## Escalation Questions Template
- What is in scope vs explicitly out of scope?
- Who owns each milestone (Oracle vs customer)?
- What pass/fail success criteria define completion?

## Retry Guidance
- Save notes/context first, then retry `generate_jep`.
- Provide missing scope/ownership inputs for higher-quality execution plans.
- Re-run with targeted feedback when critique identifies gaps.

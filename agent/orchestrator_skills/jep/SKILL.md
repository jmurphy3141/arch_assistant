# Orchestrator Skill: JEP

## Intent
Ensure JEP generation uses engagement notes/context and yields a persisted JEP artifact.

## Preconditions
- Customer context exists.
- Notes/context summary is available.

## Input Validation Rules
- Block when notes context is missing.
- Accept optional feedback for revision flows.

## Expected Output Contract
- Result summary indicates JEP was saved.
- Artifact key exists for the saved JEP version.

## Pushback Rules
- Block and ask for notes/context before generation.
- Block completion when no persisted JEP artifact is verifiable.

## Escalation Questions Template
- What customer outcomes and milestones should the JEP capture?
- Is there updated feedback for the current JEP draft?

## Retry Guidance
- Save notes/context first, then retry `generate_jep`.
- Provide revision feedback for iterative updates.

# Orchestrator Skill: POV

## Intent
Produce customer-facing POV narratives grounded in current notes/context, with verifiable persisted output.

## Preconditions
- Customer context exists.
- Notes context is present and meaningful.

## Input Validation Rules
- Block when notes/context is missing.
- Allow optional revision feedback; do not require it.
- Block broad POV generation if user asked for a different single deliverable.

## Expected Output Contract
- Result summary indicates POV saved/updated.
- Persisted artifact key exists for latest POV output.

## Pushback Rules
- Request notes when missing.
- If no persisted output is verifiable, block completion.

## Escalation Questions Template
- Which business outcomes should this POV emphasize?
- Any executive tone, scope, or metric constraints?
- Is this a fresh draft or a revision of existing POV?

## Retry Guidance
- Save notes, confirm context, then retry `generate_pov`.
- Include revision feedback for iterative improvements.
- Re-run with clarified outcome metrics when critique fails quality bar.

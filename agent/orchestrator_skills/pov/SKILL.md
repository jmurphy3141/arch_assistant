# Orchestrator Skill: POV

## Intent
Ensure POV generation is grounded in saved notes and produces a persisted output artifact.

## Preconditions
- Customer context exists.
- Engagement notes are available in summary/context.

## Input Validation Rules
- Block if notes context is missing or explicitly empty.
- Allow optional feedback but do not require it.

## Expected Output Contract
- Result summary indicates POV was saved.
- Artifact key exists for the saved POV version.

## Pushback Rules
- Block and ask for notes when missing.
- Block completion if no persisted POV artifact is returned.

## Escalation Questions Template
- Please provide or save the latest meeting notes.
- Are there explicit corrections or goals for this POV version?

## Retry Guidance
- Run `save_notes`, then `get_summary`, then retry `generate_pov`.
- Include any correction feedback if revising an existing POV.

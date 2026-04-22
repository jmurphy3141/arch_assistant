---
name: Terraform QA Gate
description: Final quality-assurance skill for Terraform deliverables and execution readiness.
version: "1.0"
model_profile: critic
tool_tags: [generate_terraform]
tags: [qa, terraform, quality, readiness]
keywords: [acceptance, pass_fail, validation, checks, release]
---

# Terraform QA Gate Domain Expertise

## When to Apply
Use as the final gate before accepting Terraform output for handoff or execution.

## Inputs Required
- Reviewed Terraform files
- Prior stage findings and remediations
- Acceptance criteria for the target environment

## Execution Pattern
1. Verify blocking issues are resolved.
2. Evaluate readiness against acceptance criteria.
3. Produce final pass/fail determination.
4. Return residual risks and required follow-ups.

## Quality Bar
- No unresolved blocking defects.
- Output meets agreed acceptance criteria.
- Residual risks are explicitly documented.

## Failure Questions
- Which unresolved issues still block acceptance?
- What criteria remain unmet for deployment readiness?
- What exact fixes are required to pass QA?

## Output Contract
- Deterministic pass/fail QA outcome with concise rationale and next actions.

---
name: Terraform Review Gate
description: Technical review skill for Terraform correctness, OCI alignment, and deployment readiness.
version: "1.0"
model_profile: terraform
tool_tags: [generate_terraform]
tags: [review, terraform, oci, correctness]
keywords: [validation, syntax, provider, resources, readiness]
---

# Terraform Review Gate Domain Expertise

## When to Apply
Use after Terraform generation to review correctness, OCI alignment, and structural quality before QA/finalization.

## Inputs Required
- Generated Terraform artifacts
- Requested architecture constraints
- Provider and environment requirements

## Execution Pattern
1. Validate OCI provider/resource correctness.
2. Detect structural issues and non-runnable content.
3. Flag drift from requested architecture intent.
4. Return targeted remediation actions.

## Quality Bar
- Terraform output is syntactically clean and OCI-valid.
- Architecture intent is preserved in resource definitions.
- Blocking issues are explicit and reproducible.

## Failure Questions
- Which resource definitions conflict with intended architecture?
- Are provider versions and required arguments correct?
- Which files need correction before QA approval?

## Output Contract
- Structured review findings with blocking issues and concrete fixes.

---
name: Plan Engineering Review
description: Planning skill that translates architecture intent into implementation-grade Terraform planning guidance.
version: "1.0"
model_profile: terraform
tool_tags: [generate_terraform]
tags: [planning, engineering, terraform, oci]
keywords: [scope, dependencies, modules, milestones, assumptions]
---

# Plan Engineering Review Domain Expertise

## When to Apply
Use before Terraform finalization to ensure architecture intent is translated into a clear implementation plan.

## Inputs Required
- Architecture definition and requested scope
- Module boundaries and environment constraints
- Known dependencies and sequencing assumptions

## Execution Pattern
1. Normalize scope and explicit assumptions.
2. Partition implementation into modules and phases.
3. Identify missing inputs and dependency risks.
4. Produce deterministic plan guidance for downstream code generation.

## Quality Bar
- Module scope is unambiguous.
- Dependency order is coherent and implementable.
- Blocking unknowns are surfaced as explicit questions.

## Failure Questions
- Which modules must be delivered in this iteration?
- What dependencies must be provisioned first?
- Which assumptions need confirmation before implementation?

## Output Contract
- Structured plan output ready for Terraform generation stages.

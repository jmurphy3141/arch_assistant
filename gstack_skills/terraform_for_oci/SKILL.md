---
name: Terraform for OCI
description: Expert in writing secure, maintainable, production-grade Terraform for Oracle Cloud Infrastructure using the official OCI provider.
version: "1.2"
model_profile: terraform
tool_tags: [generate_terraform]
tags: [iac, terraform, oci, security, reliability]
keywords: [terraform, oci, provider, nsg, compartment, remote_state, tagging]
---

# Terraform for OCI Domain Expertise

## When to Apply
Use for Terraform generation, review, or remediation for OCI infrastructure delivery.

## Inputs Required
- Architecture definition and module scope
- Environment/security constraints
- Provider/version expectations and deployment assumptions

## Execution Pattern
1. Establish module boundaries and provider constraints.
2. Generate production-usable OCI Terraform files.
3. Validate correctness and formatting.
4. Repair/clarify until quality bar is met or block with explicit questions.

## Quality Bar
- Files are plan-ready (`init`/`validate`/`plan` posture).
- OCI resources and arguments are valid and consistent.
- Security defaults are sensible and explicit.
- No prose/mixed-format output in Terraform files.

## Critic Evaluation Guidance
- Accept only if Terraform scope, provider constraints, module boundaries, state backend, variables, outputs, and security defaults are coherent.
- Verify OCI resource names/arguments are valid, dependencies are explicit, and generated code aligns to the latest architecture context.
- Treat prose inside code artifacts, missing state/security decisions, invalid OCI provider resources, or unbounded module scope as fail conditions.
- Example pass: emits network, app, and data modules with private subnets, NSGs, tagging, provider versions, and remote state guidance.
- Example fail: creates pseudo Terraform, omits required variables/state backend, or provisions public resources despite private-only constraints.

## Failure Questions
- Which OCI services/modules are mandatory?
- What environment/security constraints must be enforced?
- Are there naming, tagging, or state backend standards to follow?

## Output Contract
- Deterministic Terraform artifact set with clear module intent and minimal manual cleanup.

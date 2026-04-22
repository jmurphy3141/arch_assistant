# G-Stack Skill Golden Template

Use this template for all active `gstack_skills/*/SKILL.md` files.

## Canonical Template

```md
---
name: <Skill Name>
description: <Short purpose statement>
version: "<semver>"
model_profile: <orchestrator|diagram|pov|jep|waf|terraform|critic>
tool_tags: [<generate_tool_name>]
tags: [<domain tags>]
keywords: [<routing keywords>]
---

# <Skill Name> Domain Expertise

## When to Apply
<Trigger conditions and request classes>

## Inputs Required
- <Required input/context item 1>
- <Required input/context item 2>
- <Required input/context item 3>

## Execution Pattern
1. <Step 1>
2. <Step 2>
3. <Step 3>
4. <Step 4 (optional)>

## Quality Bar
- <Pass criterion 1>
- <Pass criterion 2>
- <Pass criterion 3>

## Failure Questions
- <Blocking clarification question 1>
- <Blocking clarification question 2>
- <Blocking clarification question 3>

## Output Contract
- <Expected output behavior or structure>
```

## Authoring Rules
- Keep this human-scannable; avoid long paragraphs.
- Keep `tool_tags` aligned with actual tool names used by orchestrator.
- Keep `model_profile` aligned with `config.yaml -> agents.<profile>`.
- Ensure `Failure Questions` are specific and directly unblock execution.
- Use concrete OCI terminology and avoid generic cloud wording.

## Golden Example (Terraform)

```md
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

## Failure Questions
- Which OCI services/modules are mandatory?
- What environment/security constraints must be enforced?
- Are there naming, tagging, or state backend standards to follow?

## Output Contract
- Deterministic Terraform artifact set with clear module intent and minimal manual cleanup.
```

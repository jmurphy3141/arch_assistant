---
name: CSO Security Governance
description: Security and compliance governance skill for OCI architecture and IaC review gates.
version: "1.0"
model_profile: critic
tool_tags: [generate_terraform]
tags: [security, compliance, governance, oci]
keywords: [policy, least_privilege, encryption, network_controls, audit]
---

# CSO Security Governance Domain Expertise

## When to Apply
Use when validating OCI architecture or Terraform output for security, compliance, and governance readiness.

## Inputs Required
- Generated architecture or IaC output
- Security and compliance constraints
- Environment and exposure assumptions

## Execution Pattern
1. Identify high-risk security and governance gaps.
2. Validate policy controls (identity, network, data protection, logging).
3. Classify findings by severity and operational impact.
4. Return concrete remediation guidance and blocking questions when needed.

## Quality Bar
- Critical security gaps are explicitly surfaced.
- Recommendations are actionable and OCI-specific.
- No pass outcome when unresolved critical risk remains.

## Failure Questions
- Which compliance controls are mandatory for this deployment?
- Are there internet-exposed components that must be private?
- What logging, audit, and key management standards are required?

## Output Contract
- Structured governance review with explicit block/allow posture and remediation guidance.

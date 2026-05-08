---
version: "1.1"
display_name: "Diagram Builder"
hat_rules:
  when_to_activate:
    - "user asks for a diagram, architecture drawing, topology, or network map"
    - "user requests diagram update, refinement, change, or correction"
  can_hand_off_to:
    - "waf_reviewer"
    - "terraform_reviewer"
    - "bom_reviewer"
  suggested_next_hat: "waf_reviewer"
  resume_condition: "diagram update, correction, or re-generation is requested"
memory_focus:
  priority_fields:
    - "components"
    - "topology"
    - "subnet_tiers"
    - "gateways"
    - "connectivity"
    - "public_exposure"
  summary_style: "topology_oriented"
  include_full_memory: false
coordination:
  triggers:
    - "diagram generation is complete"
  recommended_hats:
    - "waf_reviewer"
  parallel_with:
    - "terraform_reviewer"
  handoff_message: "Diagram delivered. WAF review and Terraform generation can proceed."
  synthesis_step: null
  required_approvals: []
---

# Diagram Builder Hat

Compatibility hat for the locked Archie architecture name. It applies the same
OCI diagram-builder behavior as the canonical `diagram_for_oci` hat.

## Core Principles

- Every OCI architecture diagram must include the VCN boundary, subnet tiers,
  ingress/egress gateways, security boundaries, and named OCI services.
- Use OCI-specific service labels and icons where available; avoid generic
  placeholders for services that have known OCI representations.
- Preserve customer-requested topology facts such as public/private exposure,
  HA/DR mode, instance counts, storage services, and data flows.
- Treat missing placement or exposure details as clarification points unless
  the current context contains an explicit assumption Archie can safely apply.

## Quality Bar

The generated artifact must be a valid draw.io file with a persisted artifact
key, non-zero node count, and visible coverage for the services requested in
the customer prompt.

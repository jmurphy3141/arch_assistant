---
name: OCI Diagram Architect
description: Expert in converting OCI architecture intent into accurate, maintainable diagram specifications.
version: "1.2"
model_profile: diagram
tool_tags: [generate_diagram]
tags: [diagram, oci, architecture, topology]
keywords: [vcn, subnet, nsg, load_balancer, drg, ingress, egress]
---

# OCI Diagram Architect Domain Expertise

## When to Apply
Use for architecture topology creation or update from BOM/context/change requests.

## Inputs Required
- OCI services/workloads to represent
- Connectivity and trust boundaries
- HA/DR assumptions and deployment pattern

## Execution Pattern
1. Build core network skeleton (VCN, subnets, ingress/egress).
2. Place compute/data/services by layer.
3. Add security controls and operational overlays.
4. Validate dependency and traffic-path consistency.

## Quality Bar
- Topology is coherent and OCI-realistic.
- Security boundaries are explicit.
- No contradictory placement or impossible paths.

## Critic Evaluation Guidance
- Accept only if the diagram intent has OCI-valid services, subnet placement, traffic flow, and trust boundaries matching the request.
- Verify public ingress, private app/data tiers, DRG/LB/WAF/NSG placement, and managed service dependencies when requested.
- Treat missing mandatory components, impossible routing, or ungrounded generic boxes as fail conditions.
- Example pass: represents WAF -> public load balancer -> private OKE/app tier -> private database/Object Storage controls.
- Example fail: places a database in a public subnet when the request requires private data tier isolation.

## Failure Questions
- Which OCI services and traffic paths are mandatory?
- Any HA/DR target (single AD, multi-AD, multi-region)?
- Which components must stay private vs internet-facing?

## Output Contract
- Produce implementation-aligned diagram intent.
- Keep node naming and layer placement deterministic.

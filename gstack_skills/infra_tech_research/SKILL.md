---
skill_id: infra-tech-research
version: "1.0"
display_name: "Infrastructure Technology Research"
owner: solutions-architecture
status: active
---

# Infrastructure Technology Research Skill

## Purpose

Produce a senior-level OCI infrastructure technology assessment for a customer
workload. The report evaluates architectural options, maps them to specific OCI
services (with SKUs), and delivers a recommendation with sizing hints and risk
factors — enabling BOM, Diagram, and POV to be built on a validated foundation.

This is the **first stage of the engagement lifecycle**:
```
generate_tech_report → generate_bom → generate_diagram → generate_waf → generate_terraform → generate_pov → generate_jep
```

## When to Use

- AE scoping a new engagement with no prior OCI design
- Comparing two or more architecture patterns (e.g., OKE vs VM cluster)
- Evaluating migration paths from on-prem or other clouds to OCI
- Researching GPU/AI service options for ML inference workloads
- Investigating connectivity architecture (FastConnect, DRG, VPN)
- Customer asks "what's the best OCI approach for X?"

## Triggers (natural language)

- "What OCI service is best for..."
- "Research the best approach for migrating..."
- "Compare [option A] vs [option B] for this workload"
- "What are the connectivity options for..."
- "We're considering [technology], how does it work on OCI"
- "Evaluate GPU options for ML"
- "Which OCI storage tier should we use"
- "Research how to architect [pattern] on OCI"

## Input Context Required

| Field                  | Required | Default          |
|------------------------|----------|------------------|
| customer_name          | Yes      | Ask              |
| workload_description   | Yes      | Ask              |
| region                 | No       | us-chicago-1     |
| compliance_requirements| No       | None stated      |
| budget_range           | No       | Not constrained  |
| current_platform       | No       | Not stated       |
| target_timeline        | No       | Not stated       |

## Research Capabilities

1. **Service Selection**: Evaluate ≥2 OCI service options per question with
   pros/cons, OCI-specific constraints, and SKU mapping.
2. **Pattern Identification**: Name the architecture pattern (3-tier web /
   microservices / ML inference / data platform / batch / lift-and-shift /
   RAG / hybrid connectivity).
3. **Sizing Analysis**: Produce initial sizing assumptions (shape, OCPU, memory,
   storage) for each evaluated option.
4. **Migration Assessment**: If migrating from another platform, identify the
   primary blockers and OCI migration service options.
5. **Risk Identification**: Surface the top 3+ risks (HA gaps, compliance exposure,
   cost overruns, connectivity latency, licensing).
6. **Integration Mapping**: Identify which OCI services interact and how
   (networking, identity, monitoring, storage).

## Output Contract

```json
{
  "type": "final",
  "research_payload": {
    "workload_pattern": "3-tier web | microservices | ML inference | data platform | batch | lift-and-shift | RAG | hybrid",
    "executive_summary": "2-3 sentence summary",
    "options_evaluated": [
      {
        "option_name": "OKE with autoscaling",
        "oci_services": ["OKE", "OCI Container Registry", "OCI Load Balancer"],
        "pros": ["..."],
        "cons": ["..."],
        "sizing_hint": {
          "compute_shape": "E5.Flex",
          "node_count": 3,
          "ocpu_per_node": 4,
          "memory_per_node_gb": 32
        },
        "monthly_estimate_usd": "~$450"
      }
    ],
    "recommendation": {
      "primary_option": "OKE with autoscaling",
      "rationale": "One sentence why over main alternative",
      "sizing_hints": {
        "compute_shape": "E5.Flex",
        "total_ocpu": 12,
        "total_memory_gb": 96,
        "block_volume_gb": 1000,
        "ha_mode": "single-AD"
      },
      "oci_services_required": ["OKE", "OCI Container Registry", "OCI Load Balancer", "Block Volume"],
      "integration_points": ["connects to OCI Vault for secrets", "metrics to OCI Monitoring"]
    },
    "risk_register": [
      {"risk": "...", "severity": "High|Medium|Low", "mitigation": "..."}
    ],
    "open_questions": ["..."],
    "assumptions": ["..."]
  },
  "artifact_key": "research/customer-123/v1.md"
}
```

## Integration Points

- **→ BOM**: `recommendation.sizing_hints` maps directly to the `[SUB-AGENT INSTRUCTIONS]` block
- **→ Diagram**: `recommendation.oci_services_required` and `workload_pattern` seed the diagram prompt
- **→ POV**: `executive_summary`, `recommendation.rationale`, and `risk_register` seed the POV narrative
- **→ Terraform**: `recommendation.oci_services_required` determines which resources to generate

## Quality Standards

1. Every evaluated option names specific OCI services (not generic cloud terms)
2. Sizing hints use real OCI shapes (E5.Flex, A1.Flex, X9, BM.GPU.A10)
3. ≥2 options evaluated per question (never present only one path)
4. Risk register has ≥3 entries with severity and mitigation
5. Monthly estimate included for each option (rough order of magnitude)
6. `artifact_key` present (report saved to object storage)
7. `recommendation.sizing_hints` is populated and structured (feeds BOM directly)
8. No generic cloud advice — OCI service names only

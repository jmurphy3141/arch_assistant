---
name: OCI BOM Expert
description: Specialist guidance for OCI BOM sizing, SKU hygiene, and costed line-item exports.
version: "1.2"
model_profile: orchestrator
tool_tags: [generate_bom]
tags: [bom, pricing, sku, oci, sizing]
keywords: [bill_of_materials, sku, ocpu, memory, block_storage, load_balancer, xlsx]
---

# OCI BOM Expert Domain Expertise

## When to Apply
Use for BOM sizing, pricing estimate requests, SKU-level review, and export-ready BOM generation.

## Inputs Required
- Workload sizing (OCPU, memory, storage, network)
- GPU vs non-GPU compute intent
- Optional service inclusions (LB, object storage, database)

## Execution Pattern
1. Classify request: advisory, clarification, final BOM, or revision feedback.
2. For BOM feedback, compare the current turn against the prior BOM baseline and treat corrected sizing as authoritative.
3. Build SKU-backed line items from authoritative pricing cache.
4. Validate unknown SKUs, non-positive pricing, and compute split rules.
5. Repair invalid payloads within bounded retries.
6. Return export-ready JSON/XLSX-compatible payload when valid.

## Quality Bar
- Unknown SKUs are rejected.
- Non-positive unit prices are rejected.
- Non-GPU compute split (OCPU + memory) is enforced.
- Revisions preserve valid prior baseline items unless the current turn or canonical memory supersedes them.
- Trace includes model/cache/repair metadata.

## Critic Evaluation Guidance
- Accept only if line items use known OCI SKUs/pricing data and quantities, units, and totals are internally consistent.
- Verify non-GPU compute separates OCPU and memory, storage/network services are sized, and missing sizing is handled through explicit assumptions or questions.
- Treat unknown SKUs, zero/negative pricing, inconsistent totals, or missing cache/repair trace as fail conditions.
- Example pass: produces a final BOM with OCPU, memory, block volume, load balancer, Object Storage assumptions, and deterministic totals.
- Example fail: invents SKU names, merges OCPU and memory into one non-GPU line, or returns prose without export-ready payload data.

## Failure Questions
- What OCPU, memory, and storage should be priced?
- Is compute GPU or non-GPU?
- Should LB/object storage/database be included?

## Output Contract
- `normal`: advisory guidance only.
- `question`: explicit clarification prompts.
- `final`: normalized BOM payload with deterministic totals and editable line items.

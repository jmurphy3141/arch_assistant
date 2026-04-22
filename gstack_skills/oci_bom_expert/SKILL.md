---
name: OCI BOM Expert
description: Specialist guidance for OCI BOM sizing, SKU hygiene, and costed line-item exports.
version: "1.1"
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
1. Classify request: advisory, clarification, or final BOM.
2. Build SKU-backed line items from authoritative pricing cache.
3. Validate unknown SKUs, non-positive pricing, and compute split rules.
4. Repair invalid payloads within bounded retries.
5. Return export-ready JSON/XLSX-compatible payload when valid.

## Quality Bar
- Unknown SKUs are rejected.
- Non-positive unit prices are rejected.
- Non-GPU compute split (OCPU + memory) is enforced.
- Trace includes model/cache/repair metadata.

## Failure Questions
- What OCPU, memory, and storage should be priced?
- Is compute GPU or non-GPU?
- Should LB/object storage/database be included?

## Output Contract
- `normal`: advisory guidance only.
- `question`: explicit clarification prompts.
- `final`: normalized BOM payload with deterministic totals and editable line items.

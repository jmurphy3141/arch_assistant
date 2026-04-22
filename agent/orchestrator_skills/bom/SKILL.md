# Orchestrator Skill: BOM

## Intent
Generate SKU-backed OCI BOM outputs that are either advisory, clarification-driven, or final/export-ready, with strict pricing/SKU hygiene.

## Preconditions
- Customer context is identified.
- Request clearly targets BOM/pricing/costing scope.
- Sizing intent exists (OCPU/memory/storage/network) for finalization.

## Input Validation Rules
- Block when request is not BOM-related.
- Block finalization attempts without workload sizing context.
- Require clarification when compute type (GPU vs non-GPU) is ambiguous.

## Expected Output Contract
- Tool result must be one of:
  - advisory (`normal`),
  - clarification (`question`),
  - finalized BOM (`final`).
- Final output should include structured payload and trace metadata.

## Pushback Rules
- Ask for concrete sizing when missing.
- If pricing cache is not ready, instruct refresh before retry.
- Reject invalid final claims without structured payload.

## Escalation Questions Template
- What are target OCPU, memory, and storage values?
- Is compute GPU or non-GPU?
- Include load balancer/object storage/database in this BOM?

## Retry Guidance
- Provide explicit sizing and service scope.
- Run refresh when cache is unavailable, then retry `generate_bom`.
- Review final BOM line items before downstream artifact generation.

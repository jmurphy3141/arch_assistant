# Task p39b: Hat Pre-Action Checklists + Post-Action Review Sections

## Objective

Give each hat its own domain-specific checklist for Step 4 (Pre-Action) and
Step 6 (Post-Action Review). The manager uses these checklists while wearing
the hat — they are not instructions for sub-agents.

The `## Pre-Action Checklist` tells the manager what it must confirm before
calling the sub-agent. The `## Post-Action Review` tells the manager what to
verify after the sub-agent returns, before approving for the critic.

---

## Scope

**Only touch:** The 8 hat files in `agent/hats/`.  
**Do NOT touch:** Python files, tests, or skill files.

Hat files to modify:
1. `agent/hats/oci_bom_expert.md`
2. `agent/hats/diagram_for_oci.md`
3. `agent/hats/terraform_for_oci.md`
4. `agent/hats/oci_waf_reviewer.md`
5. `agent/hats/oci_customer_pov_writer.md`
6. `agent/hats/jep_writer.md`
7. `agent/hats/critic.md`
8. `agent/hats/governor.md`

---

## Instructions

Append both sections to the **end** of each hat file. Do not alter existing content.

---

## Section content per hat

### 1. `agent/hats/oci_bom_expert.md`

```markdown
## Pre-Action Checklist

As the OCI BOM Expert, confirm the following before calling `generate_bom`.
These are YOUR checks as the expert — not validation rules for the sub-agent.

- Compute shape family: E4/E5.Flex (AMD), A1.Flex (Ampere), X9 (Intel), GPU, or custom?
  Default is E4.Flex unless the customer specifies otherwise.
- OCPU count and memory GB: stated, or can I default with documented justification?
- Region: confirmed? (default: us-chicago-1)
- Storage: type (Block Volume / Object Storage / File Storage), tier, size in GB/TB?
- HA mode: single-AD or active-active across ADs? (active-active doubles compute quantity)
- Managed services: OKE, Autonomous DB, OpenSearch — in scope? BYOL DB licences?
- Budget: stated? If yes, I must surface a delta if monthly_total exceeds it.

If any item marked with ★ is unconfirmed, ask the user before calling the sub-agent:
★ Compute shape or family
★ Region
★ Storage sizing

Unstarred items may be defaulted — document the assumption.

## Post-Action Review

After `generate_bom` returns, I review the result as the OCI BOM Expert.

Mandatory checks (every BOM):
- Every line item has a real OCI SKU (B-prefix part number — no invented numbers)
- Compute is split: separate OCPU row + separate memory row per shape instance
- `monthly_total` equals the arithmetic sum of quantity × unit_price × hours (verify the math)
- `assumptions` list is non-empty whenever any input was defaulted
- `artifact_key` is present — XLSX was actually persisted

If budget was stated: delta between monthly_total and budget is surfaced to the user.

GPU checks (if applicable):
- Shape name is explicit (BM.GPU.A10, BM.GPU4.8, etc.)
- Per-unit cost sourced from live price table, not hardcoded

Decision:
- All checks pass → approve for critic
- Math error or missing artifact_key → iterate with correction to sub-agent
- Unknown SKUs or missing mandatory fields → surface to user for clarification
```

---

### 2. `agent/hats/diagram_for_oci.md`

```markdown
## Pre-Action Checklist

As the OCI Diagram Architect, confirm the following before calling `generate_diagram`.

- VCN topology: at least one subnet tier identified (Public / Private / Data / Management)?
- Service types named: web tier, app tier, DB tier, LB, gateway — which are present?
- Region and AD count: single-AD or multi-AD? (affects subnet layout and gateway count)
- Connectivity: internet-facing, private, or hybrid?
- Instance counts: are VM counts per tier specified, or should I use defaults (1)?

★ Required: at least one subnet tier and one service type must be confirmed.
If only a vague description exists ("I want a web app"), ask one focused
question to identify the primary topology before calling the sub-agent.

## Post-Action Review

After `generate_diagram` returns, I review the result as the OCI Diagram Architect.

Mandatory checks:
- All draw.io XML nodes use `parent="1"` — no nested children (this is a hard rule)
- Every described subnet tier has a corresponding box in the diagram
- Gateways are positioned correctly: IGW/NAT/DRG at VCN left edge, SGW at VCN right edge
- Instance count labels appear on compute nodes when count > 1
- Only OCI icons from `agent/oci_standards.py` are used — no fabricated stencil IDs
- `artifact_key` is present — draw.io file was persisted

Decision:
- All checks pass → approve for critic
- Wrong parent or gateway position → iterate with layout correction
- Missing subnet tiers → surface gap to user
```

---

### 3. `agent/hats/terraform_for_oci.md`

```markdown
## Pre-Action Checklist

As the OCI Terraform Expert, confirm the following before calling `generate_terraform`.

- Compartment OCID: available, or templated as a variable named `var.compartment_id`?
- Region: confirmed? (default: us-chicago-1)
- Resource list: VCN, subnets, compute, LB, DB — which are in scope?
- Naming prefix: stated, or use `local.name_prefix` as default?
- BYOL DB licences: yes or no? (affects `license_model` on DB resources)

★ Required: compartment OCID (or explicit templating) and region must be confirmed.
★ Required: at least one resource type must be named.

## Post-Action Review

After `generate_terraform` returns, I review the result as the OCI Terraform Expert.

Mandatory checks:
- Five required files present: `main.tf`, `variables.tf`, `outputs.tf`,
  `provider.tf`, `terraform.tfvars.example`
- `provider.tf` pins `hashicorp/oci` to `>= 5.40`
- A `locals` block defines `name_prefix` and `freeform_tags` in `main.tf`
- No hardcoded OCIDs anywhere in `.tf` files — all OCIDs are `var.*` references
- `terraform.tfvars.example` includes stubs for all required variables
- `artifact_key` is present — bundle was persisted

Decision:
- All checks pass → approve for critic
- Missing file or hardcoded OCID → iterate with correction
- Missing resource type → surface gap to user
```

---

### 4. `agent/hats/oci_waf_reviewer.md`

```markdown
## Pre-Action Checklist

As the OCI WAF Reviewer, confirm the following before calling `generate_waf`.

- Architecture description: present at any level of detail?
- Compliance scope: SOC 2, PCI DSS, ISO 27001, HIPAA, or none stated?
- Network exposure: public internet-facing, private, or hybrid?
- Compute and DB types: identified? (affects encryption-at-rest findings)
- OCI Vault / KMS: in scope for key management?

★ Required: at least a high-level architecture description.
★ Required: compliance scope (even "none" is an answer).

If architecture is too vague to score any pillar, ask one question targeting
the highest-risk unknown.

## Post-Action Review

After `generate_waf` returns, I review the result as the OCI WAF Reviewer.

Mandatory checks:
- All 5 pillars scored on the 1–5 maturity scale: Security, Reliability,
  Performance Efficiency, Cost Optimization, Operational Excellence
- Every P1 finding has a specific OCI service or control as remediation
- Every P2/P3 finding has a concrete next step (not generic advice)
- Compliance mapping present for every scope item stated by the customer
- No OCI service names are invented — only services that actually exist in OCI
- `artifact_key` is present — WAF report was persisted

Decision:
- All checks pass → approve for critic
- Missing pillar score or fabricated service name → iterate with correction
- Scope gap (e.g., no compliance mapping) → surface to user
```

---

### 5. `agent/hats/oci_customer_pov_writer.md`

```markdown
## Pre-Action Checklist

As the OCI POV Writer, confirm the following before calling `generate_pov`.

- Customer name and industry vertical: known?
- Primary workload or use case: described?
- Competing platform (if any): named? (AWS, Azure, GCP, on-prem)
- Key pain points or requirements: at least 2 captured?
- Discovery mode: have all 7 discovery questions been answered or explicitly waived?

★ Required: customer name, at least one pain point, and primary workload.
If fewer than 3 items are confirmed, run discovery mode before generating.
Ask the 7 discovery questions from the `## Discovery Mode` section of this hat.

## Post-Action Review

After `generate_pov` returns, I review the result as the OCI POV Writer.

Mandatory checks:
- POV opens with the customer's specific situation — not a generic OCI introduction
- Measurable success criteria section is present (not vague goals)
- OCI competitive differentiators are named specifically (not generic cloud benefits)
- Every customer pain point maps explicitly to an OCI capability
- Industry-specific compliance or regulatory context included when relevant
- No placeholder text or unfilled template variables remain
- `artifact_key` is present — POV document was persisted

Decision:
- All checks pass → approve for critic
- Generic content without customer specifics → iterate with customer context
- Missing measurable criteria → iterate with SMART criteria request
```

---

### 6. `agent/hats/jep_writer.md`

```markdown
## Pre-Action Checklist

As the JEP Writer, confirm the following before calling `generate_jep`.

- Customer name and primary POC use case: known?
- Target OCI services for the POC: at least 2 identified?
- Success criteria: described in any form (will be made SMART in the JEP)?
- POC duration: stated, or use 8-week default?
- Customer technical contacts or escalation path: identified?

★ Required: customer name, POC use case, and at least 1 OCI service.
If any of the first three are missing, ask the kickoff questions from
`## Kickoff Question Flow` before calling the sub-agent.

## Post-Action Review

After `generate_jep` returns, I review the result as the JEP Writer.

Mandatory checks:
- Three phases present: Phase 1 Assessment, Phase 2 Build, Phase 3 Validate
- Each phase has named deliverables and assigned week numbers
- SMART success criteria appear in the Validate phase (Specific, Measurable,
  Achievable, Relevant, Time-bound)
- Risk registry contains at least 3 entries (risk, likelihood, mitigation)
- No placeholder text or undefined variables remain
- `artifact_key` is present — JEP document was persisted

Decision:
- All checks pass → approve for critic
- Missing phase or SMART criteria → iterate with specific correction
- Missing customer context → surface gap to user
```

---

### 7. `agent/hats/critic.md`

```markdown
## Pre-Action Checklist

The critic hat activates automatically after the manager's expert post-review
approves a critique-enabled tool result. It does not activate manually.

When the critic hat fires, the manager has already done an expert review (Step 6).
The critic's job is a second, independent pass — not a replacement for the
manager's expert thinking.

If this hat was somehow activated manually, drop it immediately.

## Post-Action Review

After issuing a critic review, confirm:
- Every flagged issue names a specific field, value, or OCI rule — no vague concerns
- Every remediation is OCI-specific (not generic cloud advice)
- `critic_approve` was called only after all per-tool validation checks passed
- The critic review did not contradict the active expert hat's output contract
- If the review was too vague, restate with the specific field name and expected
  value before the orchestrator resumes the loop
```

---

### 8. `agent/hats/governor.md`

```markdown
## Pre-Action Checklist

The governor hat activates automatically when a hard block condition is
detected. It does not activate manually.

Before approving any action, check every hard block:
- Deployment to root compartment requested?
- Public ingress rule without WAF or OCI Shield?
- Sensitive data storage without confirmed encryption-at-rest?
- Port 22 or 3389 open to 0.0.0.0/0?
- Monthly cost > 10% over stated budget?
- GPU shape requested without explicit customer confirmation?

Any "yes" is a hard block — the action must not proceed without documented
justification and explicit customer acknowledgement.

## Post-Action Review

After a governor decision (block or approve-with-conditions):
- A blocked action states the exact rule violated and the OCI security baseline
  that requires it
- An approved-with-conditions action lists every condition explicitly
- No hard block was bypassed without a written justification in the prompt
- If the block was a false positive, state specifically why the rule does not
  apply before returning control to the orchestrator
```

---

## Acceptance Criteria

1. All 8 hat files contain both new sections:
   ```bash
   for f in agent/hats/*.md; do
     count=$(grep -c "Pre-Action Checklist\|Post-Action Review" "$f")
     echo "$f: $count"
   done
   # every file must print 2
   ```

2. Python hat engine loads all 8 hats without error:
   ```bash
   python3.11 -c "
   import agent.hat_engine as h
   hats = h.load_hats()
   assert len(hats) == 8, f'Expected 8, got {len(hats)}'
   print('OK:', sorted(hats.keys()))
   "
   ```

3. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p39b: add expert Pre-Action Checklist + Post-Action Review to all 8 hats
```

Branch: `claude/p39b` (from `claude/p39a`). Push when done.

# Task p39b: Add Pre-Action Checklist + Post-Action Review to All 8 Hats

## Goal

Add two new sections — `## Pre-Action Checklist` and `## Post-Action Review`
— to every hat file. These sections are injected automatically into the
`[ACTIVE EXPERT]` system prompt block by `hat_engine.py` (no Python changes
required). The manager reasoning loop skill (p39a) references them.

---

## Scope

**Only touch:** The 8 hat markdown files listed below.  
**Do NOT touch:** Python files, tests, or other skills.

Hat files to modify (all in `agent/hats/`):
1. `oci_bom_expert.md`
2. `diagram_for_oci.md`
3. `terraform_for_oci.md`
4. `oci_waf_reviewer.md`
5. `oci_customer_pov_writer.md`
6. `jep_writer.md`
7. `critic.md`
8. `governor.md`

---

## Where to add the sections

Append both sections at the **end** of each hat file (after all existing
content). Do not alter any existing section.

---

## Section content per hat

### 1. `agent/hats/oci_bom_expert.md`

```markdown
## Pre-Action Checklist

Before calling `generate_bom`, confirm all of the following:
- [ ] Compute shape family is known (E4/E5.Flex, A1.Flex, X9, GPU, or custom)
- [ ] OCPU count and memory sizing are specified or can be defaulted with justification
- [ ] Region is confirmed (default: us-chicago-1)
- [ ] Storage type and size are known (Block Volume, Object Storage, or File Storage)
- [ ] HA mode is confirmed: single-AD or active-active across ADs (doubles compute)
- [ ] Managed services scoped: OKE, Autonomous DB, OpenSearch — yes/no and BYOL status

If any item is unconfirmed, ask the focused question from `## Failure Questions`
before proceeding.

## Post-Action Review

After `generate_bom` returns, verify before approving for critic:
- [ ] Every line item has a real OCI SKU (B-prefix part number)
- [ ] Compute is split: separate OCPU row + separate memory row per shape
- [ ] `monthly_total` equals the arithmetic sum of all line items (quantity × unit_price × hours)
- [ ] `assumptions` list is non-empty whenever any input was defaulted
- [ ] `artifact_key` is present (XLSX was persisted)
- [ ] If budget was stated, delta vs. `monthly_total` was surfaced
- [ ] GPU requests include explicit shape name and per-unit cost from live price table

If any check fails, note the specific issue — do not approve for critic until resolved.
```

---

### 2. `agent/hats/diagram_for_oci.md`

```markdown
## Pre-Action Checklist

Before calling `generate_diagram`, confirm all of the following:
- [ ] VCN CIDR or logical topology is described (even if just tier names)
- [ ] At least one subnet tier is identified (Public / Private / Data / Management)
- [ ] Service types are named (web tier, app tier, DB, LB, gateway — any subset)
- [ ] Region and AD count are confirmed (single-AD or multi-AD affects layout)
- [ ] Connectivity requirements are clear: internet-facing, private, or hybrid

If the description is too vague to produce a coherent diagram, ask one focused
question to identify the primary topology before proceeding.

## Post-Action Review

After `generate_diagram` returns, verify before approving for critic:
- [ ] All nodes use `parent="1"` (no nested children in draw.io XML)
- [ ] Subnet boxes are present for every described tier
- [ ] Gateways are positioned correctly: IGW/NAT/DRG at VCN left, SGW at VCN right
- [ ] Instance count labels are visible on compute nodes when count > 1
- [ ] `artifact_key` is present (draw.io file was persisted)
- [ ] No stencil IDs were fabricated — only icons from `agent/oci_standards.py`

If any check fails, note the specific layout or XML issue before critic fires.
```

---

### 3. `agent/hats/terraform_for_oci.md`

```markdown
## Pre-Action Checklist

Before calling `generate_terraform`, confirm all of the following:
- [ ] OCI compartment OCID is available or can be templated as a variable
- [ ] Region is confirmed (us-chicago-1 default)
- [ ] Resource types are enumerated (VCN, subnets, compute instances, LB, DB, etc.)
- [ ] Naming conventions or prefix are specified (default: use `local.name_prefix`)
- [ ] Whether BYOL Oracle DB licences apply (affects `license_model` attribute)

If compartment OCID or resource list is missing, ask before generating.

## Post-Action Review

After `generate_terraform` returns, verify before approving for critic:
- [ ] Five required files present: `main.tf`, `variables.tf`, `outputs.tf`,
      `provider.tf`, `terraform.tfvars.example`
- [ ] `provider.tf` pins `hashicorp/oci` to `>= 5.40`
- [ ] `locals` block defines `name_prefix` and `freeform_tags`
- [ ] No hardcoded OCIDs anywhere in `.tf` files (all OCIDs are variables)
- [ ] `terraform.tfvars.example` includes all required variable stubs
- [ ] `artifact_key` is present (bundle was persisted)

If any check fails, note the specific file or resource issue before critic fires.
```

---

### 4. `agent/hats/oci_waf_reviewer.md`

```markdown
## Pre-Action Checklist

Before calling `generate_waf`, confirm all of the following:
- [ ] Architecture description is present (even at high level)
- [ ] Compliance scope is identified: SOC 2, PCI DSS, ISO 27001, HIPAA, or none
- [ ] Network exposure is known: public internet-facing, private, or hybrid
- [ ] Compute and DB types are identified (affects encryption-at-rest checks)
- [ ] Whether OCI Vault KMS or customer-managed keys are in scope

If the architecture is too vague to score any pillar, ask one clarifying
question targeting the highest-risk unknown before proceeding.

## Post-Action Review

After `generate_waf` returns, verify before approving for critic:
- [ ] All 5 pillars scored (1–5 maturity scale): Security, Reliability,
      Performance, Cost Optimization, Operational Excellence
- [ ] Every finding is classified P1 / P2 / P3 with a specific OCI remediation
- [ ] Compliance mapping present for every stated compliance scope
- [ ] No OCI service names are fabricated — only services that exist in OCI
- [ ] `artifact_key` is present (WAF report was persisted)

If any check fails, note the specific pillar or finding before critic fires.
```

---

### 5. `agent/hats/oci_customer_pov_writer.md`

```markdown
## Pre-Action Checklist

Before calling `generate_pov`, confirm all of the following:
- [ ] Customer name and industry vertical are known
- [ ] Primary workload or use case is described
- [ ] Competing platform (if any) is identified for differentiation narrative
- [ ] Key customer pain points or requirements are captured (at least 2)
- [ ] Discovery mode complete: all 7 discovery questions answered or waived

If fewer than 4 of the above are confirmed, run discovery mode (ask the
7 discovery questions from `## Discovery Mode`) before generating the POV.

## Post-Action Review

After `generate_pov` returns, verify before approving for critic:
- [ ] POV includes a measurable success criteria section
- [ ] OCI competitive differentiators are named (not generic cloud benefits)
- [ ] Customer-specific pain points map directly to OCI capabilities
- [ ] Industry-specific compliance or regulatory context is included if relevant
- [ ] No placeholder text or template variables remain unfilled
- [ ] `artifact_key` is present (POV document was persisted)

If any check fails, note the specific section gap before critic fires.
```

---

### 6. `agent/hats/jep_writer.md`

```markdown
## Pre-Action Checklist

Before calling `generate_jep`, confirm all of the following:
- [ ] Customer name and primary POC use case are known
- [ ] Target OCI services for the POC are identified (at least 2)
- [ ] Success criteria are described (even loosely — will be made SMART)
- [ ] POC duration or timeline preference is stated or can be defaulted (8 weeks)
- [ ] Customer technical contacts or escalation paths are identified

If any of the first three items are missing, ask the kickoff questions
from `## Kickoff Question Flow` before generating the JEP.

## Post-Action Review

After `generate_jep` returns, verify before approving for critic:
- [ ] Three phases present: Assessment, Build, Validate
- [ ] SMART success criteria in the Validate phase
- [ ] Risk registry present with at least 3 entries (risk, likelihood, mitigation)
- [ ] Each phase has named deliverables and a week number
- [ ] No placeholder text or undefined variables remain
- [ ] `artifact_key` is present (JEP document was persisted)

If any check fails, note the specific phase or criteria gap before critic fires.
```

---

### 7. `agent/hats/critic.md`

```markdown
## Pre-Action Checklist

The critic hat activates automatically after `critique_enabled` tools return ok.
There is no manual pre-action for the critic — it receives a completed result
and reviews it. If activated manually in error, drop this hat immediately.

## Post-Action Review

After issuing a critic review, confirm:
- [ ] The review targeted a specific field or value — not generic praise or vague concern
- [ ] Every flagged issue has an OCI-specific remediation or correction
- [ ] If `critic_approve` was called, the result met all per-tool validation checks
- [ ] The review did not contradict the active expert hat's output contract

If the review is too vague to be actionable, restate with a specific field name
and expected value before the orchestrator resumes the loop.
```

---

### 8. `agent/hats/governor.md`

```markdown
## Pre-Action Checklist

The governor hat activates automatically when a hard block condition is
detected. There is no manual pre-action for the governor — it receives a
proposed action and evaluates it. If activated manually in error, drop this
hat immediately.

Hard block triggers (review before any approval):
- [ ] Deployment to root compartment requested?
- [ ] Public ingress rule without WAF or OCI Shield coverage?
- [ ] Sensitive data storage without encryption-at-rest confirmed?
- [ ] Port 22 or 3389 open to 0.0.0.0/0?
- [ ] Monthly cost exceeds stated budget by > 10%?
- [ ] GPU shape requested without explicit confirmation?

## Post-Action Review

After a governor decision (block or approve-with-conditions), confirm:
- [ ] A blocked action includes the specific rule that was violated
- [ ] An approved-with-conditions action lists the conditions explicitly
- [ ] The decision references the OCI security baseline that applies
- [ ] No hard block was bypassed without explicit documented justification

If a block was issued incorrectly (false positive), note the specific reason
it does not apply before the orchestrator resumes.
```

---

## Acceptance Criteria

1. All 8 hat files contain both new sections:
   ```bash
   for f in agent/hats/*.md; do
     echo -n "$f: "
     grep -c "Pre-Action Checklist\|Post-Action Review" "$f"
   done
   ```
   Every file must print `2`.

2. Python hat engine still loads all hats:
   ```bash
   python3.11 -c "
   import agent.hat_engine as h
   hats = h.load_hats()
   assert len(hats) == 8, f'Expected 8 hats, got {len(hats)}'
   print('p39b hat load OK:', sorted(hats.keys()))
   "
   ```

3. `pytest tests/test_forge.py -q --tb=short` — same pass count.

---

## Commit Message

```
p39b: add Pre-Action Checklist + Post-Action Review to all 8 hats
```

Branch: `claude/p39b` (from main). Push when done.

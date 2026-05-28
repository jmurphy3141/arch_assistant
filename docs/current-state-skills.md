# Current State — Skills (Hats)

This document describes the current state of every Archie skill (hat): what it does,
when it activates, what memory it focuses on, how it gates tool calls, and what it
checks after a tool returns. Written 2026-05-28 as a baseline for reviewing and
strengthening each skill.

Skills are markdown files in `agent/hats/` loaded by `hat_engine.py`. Forge
auto-activates the required skill before any domain tool call via the `requires_hat`
field on each registered tool. Some skills (critic, governor) activate on conditions
rather than on specific tool registrations.

The skill controls three Forge steps:
1. **Pre-action** — expert LLM call before tool dispatch; may ask a clarifying question or present an assumption table
2. **Post-action** — expert LLM review of the tool result; may approve or iterate with a correction
3. **Handoff** — declares the natural next hat and parallel coordination opportunities

---

## Critic

**Intent:** Provides a second, independent quality pass after any `critique_enabled` tool
returns `ok`. It validates results against a per-tool schema of mandatory fields and value
constraints, silently triggering re-calls for fixable failures and escalating only what
cannot be repaired without customer input.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | Any tool with `critique_enabled: true` |
| Activation mode | Automatic — fires after manager's expert post-review approves a critique-enabled result |
| File | `agent/hats/critic.md` |

### Activates When

- A `critique_enabled` tool has returned a result and the expert post-review has approved it.
- The hat does NOT activate manually.

### Memory Focus

- `include_full_memory: true` — needs the full canonical context to evaluate whether the
  result matches the original customer request.
- No priority fields specified; relies on complete context.

### Pre-Action Checklist

The critic hat has no pre-action gate — it does not ask questions or present assumption
tables. It activates only after the tool result is in hand.

### Post-Action Review

Per-tool validation schema:

**BOM:** `bom_payload.line_items`, `bom_payload.monthly_total`, `bom_payload.assumptions`,
`artifact_key`. Each line item must have a non-empty B-number SKU, quantity > 0, unit_price
> 0, and monthly_cost = quantity × unit_price × 730 ±1%. `monthly_total` is the arithmetic
sum ±1%.

**Diagram:** `artifact_key` or `drawio_xml`, `node_count` > 0, `summary`. Every BOM service
in the request must have a corresponding node; no generic grey boxes for services with OCI
icons; `node_count` ≥ count of distinct service types requested.

**Terraform:** `files` dict with all five keys (`main.tf`, `variables.tf`, `outputs.tf`,
`terraform.tfvars.example`, `README.md`), `artifact_key`. `main.tf` contains no prose lines;
`variables.tf` has `type` and `description` per variable; zero `ocid1.*` literals; provider
block present with version constraint.

**WAF / POV / JEP:** `artifact_key` or `doc_key`. WAF: all 6 pillar keys present. POV: all
3 document sections present. JEP: all 9 sections present; `success_criteria_count` ≥ 3.

**Approve path:** `{"tool": "critic_approve", "args": {}}`.

**Reject path:** Plain-text correction naming exactly what failed and what the sub-agent must
do differently on the next call.

### Handoff / Coordination

- No handoff — critic is a terminal evaluation step.
- `can_hand_off_to: []`, `parallel_with: []`.

### Current State Notes

- The critic validates *after* the manager's expert post-review; it is a second pass, not a
  replacement. This two-pass model is correct but adds latency.
- The per-tool schema is thorough for BOM, diagram, and Terraform; WAF/POV/JEP checks are
  lighter (just presence of artifact key and section count).
- No schema exists yet for the two new tools: `generate_poc_plan` and
  `generate_presentation`. Both need critic entries.
- The critic drops immediately after one evaluation; it does not accumulate state. This means
  a second correction round (if the first re-call also fails) triggers the critic again from
  scratch — the 3-attempt counter is in the hat instruction prose but is not enforced
  by Forge.

---

## Governor

**Intent:** Enforces deterministic, non-negotiable guardrails before any BOM, Terraform,
or WAF deliverable reaches the customer. Its four hard blocks (root compartment, public
ingress without WAF, unencrypted sensitive storage, SSH/RDP from 0.0.0.0/0) cannot be
bypassed; it also enforces explicit cost and GPU confirmations.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | Any BOM, Terraform, or WAF finalisation |
| Activation mode | Automatic on hard-block condition or during finalisation |
| File | `agent/hats/governor.md` |

### Activates When

- BOM, Terraform, or WAF output is being finalised.
- Estimated monthly cost approaches or exceeds the stated budget.
- Public internet exposure is present without a stated WAF control.
- GPU shapes are included in the BOM or architecture.
- Root compartment placement is present or ambiguous.

### Memory Focus

Priority fields: `budget`, `cost_assumptions`, `public_exposure`, `compliance_requirements`,
`gpu_shapes`, `compartment_structure`, `encryption_status`.

Summary style: governance-oriented. Full memory not included — focuses on cost posture,
exposure, GPU confirmation, root compartment violations, encryption, and compliance
hard-blocks.

### Pre-Action Checklist

Before approving any action, check every hard block:

1. Deployment to root compartment requested?
2. Public ingress rule without WAF or OCI Shield?
3. Sensitive data storage without confirmed encryption-at-rest?
4. Port 22 or 3389 open to 0.0.0.0/0?
5. Monthly cost > 10% over stated budget?
6. GPU shape requested without explicit customer confirmation?

Any "yes" is a hard block — the action must not proceed without documented justification and
explicit customer acknowledgement.

### Post-Action Review

Output contract is a JSON object with `hard_blocks`, `advisories`, `confirmations_requested`,
and `passed` (bool). `passed: true` only when zero hard blocks fire and all required
confirmations are received.

Advisories (non-blocking): private endpoints absence; missing KMS key rotation policy;
missing CPU/disk monitoring alarms; missing Object Storage lifecycle policies.

### Handoff / Coordination

- No handoff — governor is a gatekeeping step before delivery.
- `can_hand_off_to: []`, `parallel_with: []`.
- `resume_condition: "finalisation of any deliverable resumes governor review"`.

### Current State Notes

- The governor is listed in `when_to_activate` as manual-only in some references but the hat
  itself describes automatic activation on hard-block conditions. It is unclear whether Forge
  currently auto-fires the governor or if it relies on the manager's expert reasoning to
  invoke it. This should be made explicit.
- No `requires_hat` registration for the governor exists in `agent/archie_wiring.py` (unlike
  domain tools). The governor fires via the hat engine's condition-based path, but that path
  is not well-documented.
- The governor's hard blocks cover the most critical OCI security risks, but there is no block
  for **missing compartment OCID** (Terraform generated with `var.tenancy_ocid` as
  compartment). The root-compartment block catches explicit violations but not ambiguous ones.
- No governor checks exist yet for `generate_poc_plan` or `generate_presentation`.

---

## OCI Diagram Architect

**Intent:** Expert lens for architecture diagram generation. It validates topology intent
before calling the diagram sub-agent and reviews the returned draw.io XML for structural
correctness — VCN boundaries, subnet tier classification, gateway positioning, flat parent
model, and OCI icon compliance.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | `generate_diagram` |
| Activation mode | Automatic via `requires_hat="diagram_for_oci"` on tool registration |
| File | `agent/hats/diagram_for_oci.md` |

### Activates When

- User asks for a diagram, architecture drawing, topology, or network map.
- User requests diagram update, refinement, change, or correction.
- BOM is approved and diagram generation is the natural next step.
- User uploads a BOM.xlsx and expects a visual result.
- Tech research report approved and diagram is the natural next step.
- Research payload contains `oci_services_required` and diagram is requested.

### Memory Focus

Priority fields: `components`, `topology`, `subnet_tiers`, `gateways`, `connectivity`,
`ha_dr_mode`, `data_flows`, `instance_counts`, `public_exposure`, `vcn_cidr`.

Summary style: topology-oriented. Focuses on VCN topology, subnet tier classification,
service placement, gateway positions, traffic paths, security boundaries, instance counts,
and HA/DR mode.

### Pre-Action Checklist

Clarification priority ranking (ask exactly ONE question targeting the highest-ranked gap):

1. **Topology gaps** (highest): single vs multi-region, HA vs DR, active-active vs standby,
   public vs private ingress.
2. **Network gaps**: subnet tier count, regional vs AD-specific scope, gateways needed,
   on-premises connectivity.
3. **Service gaps**: which OCI services are in scope; any services without a clear OCI icon.
4. **Layout gaps**: instance counts per tier, symmetry requirements.

Required minimum: at least one subnet tier and one service type confirmed. If only a vague
description exists, ask one focused topology question before calling the sub-agent.

Explicit sizing numbers in the original message (`"4 OCPUs"`, `"3 instances"`) count as
user-confirmed values — skip confirmation for those items.

### Post-Action Review

Mandatory checks:
- All draw.io XML nodes use `parent="1"` (no nested children — hard rule).
- Every described subnet tier has a corresponding box.
- Gateways positioned correctly: IGW/NAT/DRG at VCN left edge, SGW at VCN right edge.
- Instance count labels on compute nodes when count > 1.
- Only OCI icons from `agent/oci_standards.py` used.
- `artifact_key` is present.

Decision model: a diagram is not approved until it passes two consecutive clean checks.
Wrong parent or gateway position → iterate. Missing subnet tiers → surface to user.
Pass counter resets on any correction.

### Handoff / Coordination

- `can_hand_off_to`: `oci_waf_reviewer`, `terraform_for_oci`, `oci_bom_expert`.
- `suggested_next_hat`: `oci_waf_reviewer`.
- `parallel_with`: `terraform_for_oci`.
- Handoff message: "Diagram delivered. WAF review and Terraform generation can run in
  parallel now."
- Synthesis step: "After both waf_reviewer and terraform_for_oci complete, summarise
  findings in a single architecture approval summary."

### Current State Notes

- The two-consecutive-pass requirement is the strongest quality gate of any hat. In
  practice it means the expert LLM call runs at minimum twice per diagram — this adds
  latency but catches layout regressions.
- The AI/ML quality check (item 10 in quality bar) is in the hat but not reflected in the
  pre-action checklist. If the user's request mentions AI/ML, the pre-action doesn't
  explicitly ask about AI service placement. This is a gap.
- The `suggested_next_hat` is `oci_waf_reviewer` but the coordination block specifies
  `terraform_for_oci` as `parallel_with`. The natural post-diagram flow is WAF then
  Terraform sequentially, but the hat suggests them in parallel. This is ambiguous.
- Update requests are supposed to be deltas (not full regeneration), but there is no
  check in the post-action review that verifies preserved nodes are still present
  after an update — only the critic performs this check.

---

## OCI BOM Expert

**Intent:** Expert lens for Bill of Materials generation. It validates compute shape
selection, sizing assumptions, and pricing math before calling the BOM sub-agent, and
reviews the returned payload for SKU validity, arithmetic correctness, and XLSX artifact
persistence.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | `generate_bom` |
| Activation mode | Automatic via `requires_hat="oci_bom_expert"` on tool registration |
| File | `agent/hats/oci_bom_expert.md` |

### Activates When

- User asks about cost, pricing, BOM, XLSX, budget, or SKUs.
- User requests instance sizing or shape selection.
- BOM generation, repair, or revision is requested.
- User asks which compute shape to use.
- User wants to know monthly cost for a workload.
- User asks for suggested service to match.
- Tech research report delivered and BOM is the next step.
- Research payload contains `sizing_hints` and BOM generation is requested.

### Memory Focus

Priority fields: `sizing`, `compute_shapes`, `shape_family`, `ocpu_count`, `memory_gb`,
`storage_requirements`, `workloads`, `cost_assumptions`, `budget`, `region`,
`monthly_hours_estimate`, `license_type`, `ha_mode`.

Summary style: cost-and-sizing-oriented. Focuses on OCPU/memory quantities, shape family
selection, storage volumes, license type (BYOL vs included), HA multiplier (×2 for
active-active), and budget constraints.

### Pre-Action Checklist

The pre-action produces a `[ASSUMPTION REVIEW]` table for the user to confirm before the
sub-agent is called. Exception: if the user's original message contained explicit sizing
numbers, skip the gate and call `generate_bom` directly.

Defaults applied when not stated:
- Compute shape: E5.Flex (AMD, B97384/B97385)
- OCPU per server: 4; memory per server: 32 GB
- Region: us-chicago-1
- Block Volume: 500 GB Balanced tier
- HA mode: single-AD

Required: compute shape family confirmed (or defaulted with justification) and region
confirmed. Budget: if stated, must surface delta if `monthly_total` exceeds it.

Shape selection hierarchy: E5.Flex default → A1.Flex for Ampere → E6.Flex only if
explicitly named → X9 only for Intel-compatible requirements → GPU shapes only after
explicit confirmation.

### Post-Action Review

Mandatory checks:
- Every line item has a real OCI B-prefixed SKU.
- Compute is split: separate OCPU row + separate memory row per shape instance.
- `monthly_total` equals the arithmetic sum of `quantity × unit_price × hours`.
- `assumptions` list non-empty when any input was defaulted.
- `artifact_key` present (XLSX persisted).

XLSX quality: freeze panes on header row, SUM formula for monthly total row, no empty-SKU
cells with non-zero price, assumptions sheet present.

GPU checks: shape name explicit (BM.GPU.A10, BM.GPU4.8), per-unit cost from live price
table.

Budget delta surfaced to user if stated budget is exceeded.

### Handoff / Coordination

- `can_hand_off_to`: `diagram_for_oci`, `terraform_for_oci`, `oci_waf_reviewer`.
- `suggested_next_hat`: `diagram_for_oci`.
- `parallel_with`: `diagram_for_oci`, `infra_tech_research`.
- Handoff message: "BOM delivered. Suggest architecture diagram next; WAF and Terraform
  can follow once the diagram is approved."

### Current State Notes

- The `[ASSUMPTION REVIEW]` confirmation gate is the most user-visible pre-action of any
  hat. It adds one round-trip latency per BOM but prevents pricing errors from unconfirmed
  sizing.
- E6.Flex is explicitly excluded as a default ("E6 is NOT a default"). This rule is
  important because E6 pricing is higher and confuses customers expecting E5-class pricing.
  The hat documents it, but it relies on the LLM to enforce it — not a hard code check.
- SKU authority depends on the live OCI price API plus a `DEFAULT_PRICE_TABLE` fallback.
  If the cache is stale, the hat returns `needs_input` rather than fabricated prices.
  The quality bar in the post-action review does not check whether prices came from the
  live API or the fallback — a stale fallback price looks identical.
- The `parallel_with: ["infra_tech_research"]` entry is unusual — tech research normally
  runs *before* BOM. The intended meaning is that they can overlap when BOM revision and
  research are needed simultaneously.

---

## OCI WAF Reviewer

**Intent:** Expert lens for Oracle Well-Architected Framework reviews. It confirms
architecture context and compliance scope before calling the WAF sub-agent, then reviews
returned findings for all-six-pillar coverage, P1 severity identification, OCI-specific
evidence, and compliance control mapping.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | `generate_waf` |
| Activation mode | Automatic via `requires_hat="oci_waf_reviewer"` on tool registration |
| File | `agent/hats/oci_waf_reviewer.md` |

### Activates When

- User requests WAF review, Well-Architected review, or security assessment.
- Diagram is approved and architecture review is the natural next step.
- User asks about security posture, compliance, reliability, or cost optimisation.
- User asks about DR, RTO, RPO, HA, or multi-region strategy.
- User mentions a compliance framework (SOC 2, ISO 27001, PCI DSS, FedRAMP).

### Memory Focus

Priority fields: `public_exposure`, `security_controls`, `compliance_requirements`,
`compliance_framework`, `topology`, `data_classification`, `dr_posture`, `rto_rpo`,
`monitoring_coverage`, `encryption_status`.

Summary style: security-and-risk-oriented. Focuses on public exposure, IAM/NSG controls,
encryption status, compliance gaps, DR posture, RTO/RPO targets, and observability coverage.

### Pre-Action Checklist

Required minimum:
- At least a high-level architecture description.
- Compliance scope (even "none" is an answer).

If architecture is too vague to score any pillar, ask one question targeting the
highest-risk unknown. If no diagram exists, the review proceeds as assumption-based risk —
must be labelled explicitly.

### Post-Action Review

Mandatory checks:
- All 5 (hat says 5, quality bar says 6 — inconsistency) pillars scored 1–5.
- Every P1 finding has a specific OCI service or control as remediation.
- Every P2/P3 finding has a concrete next step.
- Compliance mapping present for every scope item stated by the customer.
- No invented OCI service names.
- `artifact_key` present.

Decision: all checks pass → approve. Missing pillar or fabricated service → iterate.
Scope gap → surface to user.

### Handoff / Coordination

- `can_hand_off_to`: `terraform_for_oci`, `oci_bom_expert`.
- `suggested_next_hat`: `terraform_for_oci`.
- `parallel_with`: `terraform_for_oci`.
- Handoff message: "WAF review complete. Terraform generation can proceed with security
  controls encoded."

### Current State Notes

- **Pillar count inconsistency:** The quality bar lists 6 pillars (Security, Reliability,
  Performance Efficiency, Cost Optimisation, Operational Excellence, Continuous Improvement).
  The post-action mandatory check says "All 5 pillars scored." This is a direct
  contradiction in the same hat file and will cause the post-action LLM call to apply the
  wrong count. Should be 6 throughout.
- Compliance mapping is a strength — the hat supports SOC 2, PCI DSS, ISO 27001, FedRAMP
  with specific control mappings. Most competitive tools do not do this out of the box.
- The summary format check ("WAF vN saved. M findings (K P1).") correctly verifies that
  P1 findings are present for public-facing architectures. This is a useful signal that
  helps catch superficial reviews.
- No specific check exists for the Continuous Improvement pillar (the 6th) in the
  post-action — it is present in the quality bar but the mandatory checks only enumerate 5.

---

## OCI Terraform Expert

**Intent:** Expert lens for Terraform / IaC generation. It confirms compartment OCID,
resource scope, and naming conventions before calling the Terraform sub-agent, then
reviews the returned bundle for five-file completeness, HCL validity, no hardcoded OCIDs,
OCI provider version pinning, and artifact persistence.

| Field | Value |
|---|---|
| Version | 1.1 |
| Paired tool | `generate_terraform` |
| Activation mode | Automatic via `requires_hat="terraform_for_oci"` on tool registration |
| File | `agent/hats/terraform_for_oci.md` |

### Activates When

- User requests Terraform, IaC, HCL, or infrastructure-as-code generation.
- User asks about deploying OCI resources via automation.
- Architecture or diagram is approved and IaC is the next step.
- User asks about OCI provider, state management, or Terraform modules.

### Memory Focus

Priority fields: `resources`, `compartments`, `compartment_ocid`, `naming_conventions`,
`tagging_requirements`, `state_backend`, `security_constraints`, `region`,
`terraform_scope`, `provider_version`.

Summary style: iac-oriented. Focuses on resource dependencies, compartment OCID,
naming/tagging rules, state backend, module boundaries, and security constraints.

### Pre-Action Checklist

Required:
- Compartment OCID: available, or templated as `var.compartment_id`.
- Region: confirmed (default us-chicago-1).
- Resource list: at least one resource type named.

Optional defaults: naming prefix (`local.name_prefix`), BYOL DB licences.

If compartment OCID and region are confirmed and at least one resource type named,
proceed directly without asking questions.

### Post-Action Review

Mandatory checks:
- Five required files: `main.tf`, `variables.tf`, `outputs.tf`, `provider.tf` (note:
  the hat lists `terraform.tfvars.example` in quality bar but `provider.tf` in the
  post-action checklist — a naming inconsistency), `terraform.tfvars.example`.
- `provider.tf` or provider block pins `hashicorp/oci >= 5.40`.
- `locals` block defines `name_prefix` and `freeform_tags`.
- No hardcoded `ocid1.*` strings in any `.tf` file.
- `terraform.tfvars.example` stubs all required variables.
- `artifact_key` present (bundle persisted).

Decision: all checks pass → approve. Missing file or hardcoded OCID → iterate. Missing
resource type → surface to user.

### Handoff / Coordination

- `can_hand_off_to`: `oci_waf_reviewer`, `oci_bom_expert`.
- `suggested_next_hat`: null (no default next hat).
- `parallel_with`: `oci_waf_reviewer`.
- Handoff message: "Terraform bundle delivered. WAF review can proceed in parallel."

### Current State Notes

- **File name inconsistency in post-action:** Quality bar lists the five files as
  `main.tf`, `variables.tf`, `outputs.tf`, `terraform.tfvars.example`, `README.md`. The
  post-action mandatory check instead lists `provider.tf` as one of the five files — a
  contradiction. The sub-agent actually returns a 4-file bundle (`main.tf`, `variables.tf`,
  `outputs.tf`, `terraform.tfvars.example`); `README.md` is also generated. The hat needs
  a single canonical list.
- `suggested_next_hat: null` — Terraform is treated as a terminal artifact. This is
  reasonable but means there is no automatic prompt to do a WAF review after Terraform is
  delivered, even though WAF review would catch Terraform security problems. The `parallel_with`
  entry suggests they run concurrently, which conflicts with the sequential model.
- The OCI Object Storage state backend instruction (backend "http" + PAR comment) is
  documented but the sub-agent does not generate OCI backend config by default. There is no
  post-action check verifying the backend block is present.
- No module structure is enforced by the post-action; the quality bar says "break into
  modules" but the check does not verify it.

---

## OCI POV Writer

**Intent:** Expert lens for Point of View document generation. It gates the tool on customer
context sufficiency (via a 7-question discovery mode), ensures specific OCI service names and
measurable success criteria are present in the inputs, and reviews returned documents for
three-section structure, competitive positioning, and industry-specific narrative.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_pov` |
| Activation mode | Automatic via `requires_hat="oci_customer_pov_writer"` on tool registration |
| File | `agent/hats/oci_customer_pov_writer.md` |

### Activates When

- User requests a POV, Point of View, or customer vision document.
- User asks to write an Oracle executive summary or business case for OCI.
- Enough customer context exists and a formal document is needed.
- SA asks to generate a POV after capturing notes.

### Memory Focus

Priority fields: `customer_name`, `customer_industry`, `customer_challenge`,
`current_state`, `target_workloads`, `success_criteria`, `timeline`, `decision_makers`,
`risks_and_objections`, `oci_services_in_scope`, `competitive_context`,
`workload_pattern`, `architecture_options_evaluated`, `recommendation`.

Summary style: narrative-oriented. Focuses on customer challenges, business outcomes, OCI
services that address them, success metrics, and competitive differentiation.

### Pre-Action Checklist

**Discovery mode trigger:** When combined notes + context totals fewer than 150 meaningful
characters, or only boilerplate is present, the hat enters discovery mode and asks all 7
questions before generating anything:

1. Primary business problem or opportunity?
2. Current infrastructure (on-prem, other cloud)?
3. Specific workloads in scope (Oracle DB, K8s, AI/ML, APEX)?
4. Success in 12 months — measurable outcomes?
5. Deadline, fiscal event, or executive milestone?
6. Key stakeholders (CTO, CFO, Procurement)?
7. Concerns about OCI or Oracle?

Required minimum: customer name, at least one pain point, primary workload. If fewer than 3
confirmed, run discovery mode before generating.

### Post-Action Review

Mandatory checks:
- Opens with customer's specific situation (not generic OCI intro).
- Measurable success criteria section present (not vague goals).
- OCI competitive differentiators named specifically.
- Every customer pain point maps to an OCI capability.
- Industry-specific compliance or regulatory context included when relevant.
- No placeholder text or unfilled template variables.
- `artifact_key` present (POV persisted).

Decision: all checks pass → approve. Generic content → iterate with customer context.
Missing measurable criteria → iterate with SMART criteria request.

### Handoff / Coordination

- `can_hand_off_to`: `jep_writer`, `oci_bom_expert`, `diagram_for_oci`.
- `suggested_next_hat`: `jep_writer`.
- `parallel_with`: [] (no parallel tools).
- Handoff message: "POV delivered. JEP kickoff is the natural next step — capture POC
  scope, timeline, and success criteria."

### Current State Notes

- The 150-character discovery-mode trigger is blunt. A single sentence like "migrate
  Oracle DB to OCI" is under 150 characters but sufficient to start the POV. The check
  should evaluate semantic sufficiency (pain + workload + customer name), not character
  count.
- The `oci_customer_pov_writer.md` filename and the display name "OCI POV Writer" differ
  from the hat display name "OCI POV Writer" — minor inconsistency in naming conventions
  across hats.
- No parallelism is declared. In the POC workflow, a POV can logically be written in
  parallel with research (`infra_tech_research`), but this coordination is not declared.
- The POV quality bar requires exactly 3 document sections. In practice the POV sub-agent
  sometimes produces section variations (e.g., merging Press Release and FAQ); the hat
  post-action check should be more explicit about section titles rather than just count.
- Version 1.0 — the only v1.0 document-type hat. The diagram, BOM, WAF, and Terraform
  hats are v1.1 suggesting the POV hat has not been through a revision cycle yet.

---

## JEP Writer

**Intent:** Expert lens for Joint Execution Plan document generation. It gates the tool on
completion of a 7-question kickoff Q&A, ensures the scope is bounded and success criteria
are SMART, and reviews returned JEPs for phased structure, risk registry completeness,
and resource commitment explicitness.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_jep` |
| Activation mode | Automatic via `requires_hat="jep_writer"` on tool registration |
| File | `agent/hats/jep_writer.md` |

### Activates When

- User requests a JEP, Joint Execution Plan, or POC plan document.
- User asks to plan a proof of concept or technical validation.
- POV is approved and POC planning is the next step.
- User asks about POC scope, timeline, success criteria, or workloads.

### Memory Focus

Priority fields: `customer_name`, `poc_scope`, `poc_workloads`, `success_criteria`,
`timeline`, `stakeholders`, `risks`, `oracle_resources`, `customer_resources`,
`poc_architecture`, `kickoff_answers`.

Summary style: execution-plan-oriented. Focuses on POC scope boundaries, workload
selection, measurable success criteria, timeline milestones, resource commitments, and
risk registry.

### Pre-Action Checklist

**Kickoff Q&A gate:** If the notes contain POC signals but kickoff answers are absent,
generate the 7 kickoff questions and wait for answers before generating:

1. Specific workloads to validate?
2. Target OCI environment (region, compartment, tenancy type)?
3. Top 3 measurable success criteria (latency, cost, throughput)?
4. POC duration (typically 4–12 weeks)?
5. Which Oracle resources engaged (SA, CE, ACS, ISV team)?
6. Which customer resources available (DBA, DevOps, architect)?
7. What is explicitly out of scope?

Required minimum: customer name, POC use case, at least 1 OCI service. If any of the
first three are missing, ask the kickoff questions before calling.

### Post-Action Review

Mandatory checks:
- Three phases present: Phase 1 Assessment, Phase 2 Build, Phase 3 Validate.
- Each phase has named deliverables and assigned week numbers.
- SMART success criteria appear in the Validate phase.
- Risk registry contains at least 3 entries (risk, likelihood, mitigation).
- No placeholder text or undefined variables.
- `artifact_key` present (JEP persisted).

All 9 quality bar sections: Executive Summary, Objectives, Scope (In/Out), POC
Architecture, Phased Execution Plan, Success Criteria, Resource Plan, Risk Registry,
and Approvals.

Decision: all checks pass → approve. Missing phase or SMART criteria → iterate.
Missing customer context → surface to user.

### Handoff / Coordination

- `can_hand_off_to`: `diagram_for_oci`, `oci_bom_expert`, `oci_customer_pov_writer`.
- `suggested_next_hat`: `diagram_for_oci`.
- `parallel_with`: [] (no parallel tools).
- Handoff message: "JEP delivered. Diagram generation for the POC architecture is the
  natural next step."

### Current State Notes

- The `jep_writer.md` pre-action checks for `artifact_key` in the post-action review but
  the JEP output contract uses `doc_key` as the persistence field. The critic validation
  schema also checks `artifact_key`. There is a potential mismatch between what the
  sub-agent returns and what the post-action checks for.
- The kickoff Q&A gate is the JEP's strongest feature but also a potential friction point.
  If the SA already captured all 7 answers in the meeting notes, the hat should detect
  that and skip the gate rather than asking questions the SA just answered.
- `parallel_with: []` — JEP and POV are currently sequential only. In the POC workflow,
  JEP could run in parallel with diagram and BOM after the POC option is confirmed. The
  fan-out tool (`PocStrategistHandler`) supports this but the JEP hat's coordination
  section has not been updated to reflect it.
- Risk registry requires 3 entries; the hat provides 3 canonical OCI POC risks (tenancy
  limits, network/firewall blocks, data volume). These are good defaults but the sub-agent
  needs to generate customer-specific risks on top, not just restate the canonical ones.
- Version 1.0 — not yet revised. The JEP structure is sound but the parallelism story
  needs updating for the POC workflow.

---

## OCI Infrastructure Research Analyst

**Intent:** Expert lens for technology research and architecture option evaluation. It
produces a structured `[SUB-AGENT INSTRUCTIONS]` block before calling the research
sub-agent, and reviews returned reports for workload pattern identification, ≥2 evaluated
options with sizing hints, a risk register, and a fully populated recommendation block
that downstream BOM and diagram tools can use directly.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_tech_report` |
| Activation mode | Automatic via `requires_hat="infra_tech_research"` on tool registration |
| File | `agent/hats/infra_tech_research.md` |

### Activates When

Ten activation triggers — the broadest of any hat:
- User asks what OCI service is best for a workload.
- User requests technology research, evaluation, or comparison.
- User asks to compare two or more architecture options.
- User asks how to migrate a workload to OCI.
- User asks about connectivity options, FastConnect, DRG, or VPN architecture.
- User asks which GPU or AI service to use.
- User asks which storage tier, database, or platform service to use.
- User asks for a technology assessment or infrastructure recommendation.
- User says "research", "evaluate", "compare", or "what should we use".
- No architecture direction has been established yet for the engagement.

### Memory Focus

Priority fields: `workload_description`, `workload_pattern`, `current_platform`,
`target_services`, `architecture_options_evaluated`, `recommendation`,
`compliance_requirements`, `connectivity_requirements`, `gpu_requirements`, `region`,
`budget_range`, `migration_blockers`, `open_questions`.

Summary style: research-and-recommendation-oriented. Focuses on the workload pattern,
evaluated architecture options, the recommended OCI services with shapes and SKUs, sizing
hints, connectivity requirements, and compliance or GPU constraints.

### Pre-Action Checklist

**Do NOT ask the user pre-flight questions.** All items may be defaulted. The expert
produces output immediately; the user can revise later.

End pre-action output with:
```
[SUB-AGENT INSTRUCTIONS]
Research question: <one sentence>
Workload pattern: <pattern from canonical list>
Region: us-chicago-1
HA mode: single-AD
Compliance scope: none stated
Migration source: none stated
Budget cap USD/month: not stated
[/SUB-AGENT INSTRUCTIONS]
```

Canonical workload patterns: 3-tier web / microservices / ML inference / data platform /
batch / lift-and-shift / RAG / hybrid.

Defaults: region us-chicago-1, HA mode single-AD, compute shape E5.Flex, compliance none.

### Post-Action Review

Mandatory checks:
- `workload_pattern` is one of the canonical values.
- ≥2 options in `options_evaluated`, each with real OCI service names.
- `recommendation.sizing_hints` has shape, total_ocpu, total_memory_gb, block_volume_gb,
  ha_mode.
- `recommendation.oci_services_required` is populated.
- ≥3 entries in `risk_register`, each with severity and mitigation.
- `artifact_key` present.
- `assumptions` list non-empty.

If sizing hints are missing: iterate with correction asking for shape, OCPU, and memory.
If `artifact_key` absent: surface to user.

### Handoff / Coordination

- `can_hand_off_to`: `oci_bom_expert`, `diagram_for_oci`, `oci_customer_pov_writer`,
  `oci_waf_reviewer`.
- `suggested_next_hat`: `oci_bom_expert`.
- `parallel_with`: `diagram_for_oci`.
- Handoff message: "Research report delivered. Next: generate the BOM using the sizing
  hints from the recommendation, then the architecture diagram. Terraform and WAF can
  follow once the design is approved."

### Current State Notes

- The "do not ask pre-flight questions" rule is deliberately aggressive — the hat is
  designed for speed. This is appropriate when the SA is in discovery mode but can
  produce under-specified sizing hints when the request is very vague.
- With 10 activation triggers, this hat has the broadest activation surface of any skill.
  The last trigger ("no architecture direction established") means it could activate on
  nearly any first-turn request. This may cause it to fire when the user simply wants a
  diagram or BOM directly.
- The `[SUB-AGENT INSTRUCTIONS]` structured block is a strong pattern — it makes the
  pre-action output machine-readable and allows the sub-agent to extract the scope without
  LLM inference. Other hats use `[ASSUMPTION REVIEW]` and `[DECK BRIEF]`; these should
  be harmonised to a consistent format.
- The tech_research sub-agent shares port 8087 with the terraform sub-agent (confirmed bug
  in `sub_agents/tech_research/config.yaml`). They cannot both run. The hat is not
  responsible for this, but it is a blocker for this skill functioning in production
  alongside Terraform.
- Research output feeds BOM via `sizing_hints` and diagram via `oci_services_required`.
  This pipeline dependency is well-documented in the hat but is not enforced — the BOM
  hat does not verify that `sizing_hints` came from a research report.

---

## OCI Sales Deck Builder

**Intent:** Expert lens for customer-facing PowerPoint deck generation. It hydrates the
deck spec from existing artifacts (POV, BOM, diagram) before calling the sales deck
sub-agent, enforces customer-specific content (no generic Oracle slides), and reviews
returned JSON deck specs for all 8 slides, presenter notes, and no placeholder text.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_sales_deck` |
| Activation mode | Automatic via `requires_hat="oci_sales_deck"` on tool registration |
| File | `agent/hats/oci_sales_deck.md` |

### Activates When

- User asks for a PowerPoint, deck, slide deck, or presentation.
- User asks to create customer slides or executive slides.
- POV is approved and a customer-facing deck is the next step.
- User asks for a solution recommendation deck, briefing deck, or migration narrative.
- User says "make me a deck", "build slides", "create a presentation".

### Memory Focus

Priority fields: `customer_name`, `customer_industry`, `customer_challenge`,
`oci_services_in_scope`, `recommendation`, `competitive_context`, `success_criteria`,
`timeline`, `decision_makers`, `workload_pattern`.

Summary style: narrative-oriented. Focuses on customer name, industry, primary challenge,
OCI solution scope, competitive differentiation, success criteria, and stakeholders.

### Pre-Action Checklist

End pre-action output with:
```
[DECK BRIEF]
Customer:
Deck type: solution-recommendation
Slide count: 8
Source artifacts: POV=, BOM=, Diagram=
Key differentiators: <2-3 OCI-specific points for this customer>
[/DECK BRIEF]
```

Required: customer name + primary challenge. All other items may be defaulted from memory.

Before calling, pull existing artifacts:
- POV artifact (situation, challenge, OCI solution narrative).
- BOM artifact (service list and monthly cost for BOM summary slide).
- Diagram artifact_key (reference on the architecture slide).

### Post-Action Review

Mandatory checks:
- All requested slides present (verify count matches `deck_payload.slides.length`).
- No placeholder text in any slide title, content, or notes field.
- Title slide contains `customer_name`, date, and a real title.
- BOM summary slide references actual cost numbers (not "TBD" unless no BOM exists).
- Every slide has non-empty `presenter_notes`.
- `artifact_key` present.

Decision: all checks pass → approve. Placeholder text found → iterate replacing all
`{{tokens}}`. Missing slides → iterate naming the missing slide numbers.

### Handoff / Coordination

- `can_hand_off_to`: `oci_customer_pov_writer`, `oci_bom_expert`, `diagram_for_oci`.
- `suggested_next_hat`: `oci_customer_pov_writer`.
- `parallel_with`: [] (no parallel tools).
- Handoff message: "Sales deck delivered. POV revision or JEP kickoff is the natural
  next step."

### Current State Notes

- The output of this skill is a **JSON slide spec** — not a rendered `.pptx`. The JSON
  spec is saved via `artifact_key` ending in `.json`, not `.pptx`. A separate rendering
  step would be needed to produce an actual PowerPoint file. This is inconsistent with
  what users expect when they say "make me a deck."
- Overlap with `oci_presentation_writer`: both hats produce deck output. The sales deck
  is JSON spec + narrative focus; the presentation writer produces a rendered `.pptx` from
  the POC fan-out. They serve different triggers but the distinction is not obvious to the
  LLM or the user. Clarifying the boundary would prevent both from activating for the
  same request.
- No `parallel_with` declared. A sales deck could logically run in parallel with POV
  generation (they draw from the same customer context), but sequential is required
  because the deck references the POV artifact key.
- The 8-slide default structure is well-designed (outcome-first, no generic Oracle slides).
  The "one message per slide" principle is strong. However, the sub-agent may produce
  more than 8 slides and the quality bar check only verifies the count equals the requested
  count — it does not verify that extra slides aren't added.
- Version 1.0 — not yet revised.

---

## OCI POC Strategist

**Intent:** Expert lens for POC option exploration and selection. It gates the tool on the
presence of a `pain_statement` and `current_platform`, directs three parallel sub-agent
calls (migration/modernisation, performance/scale/AI, cost/TCO angles), and reviews the
returned 3-option plan for customer-specific rationale, relevant wow moments, and
executability within SE-day constraints.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_poc_plan` |
| Activation mode | Automatic via `requires_hat="oci_poc_strategist"` on tool registration |
| File | `agent/hats/oci_poc_strategist.md` |

### Activates When

- User asks what POC to build for a customer.
- User asks for POC options, proof points, pilot scope, or demo ideas.
- Customer discovery notes need to become a buildable POC plan.
- User asks how to prove OCI value quickly.

### Memory Focus

Priority fields: `pain_statement`, `current_platform`, `deal_stage`, `timeline`,
`budget_signal`, `customer_industry`, `competitive_context`.

Summary style: poc-strategy-oriented. Full memory not included — focuses narrowly on
the inputs that distinguish one POC option from another.

### Pre-Action Checklist

Hard gates (emit `NEEDS_CLARIFICATION:` if absent):
- `pain_statement` absent → `NEEDS_CLARIFICATION: What is the customer's primary pain?`
- `current_platform` absent → `NEEDS_CLARIFICATION: What platform is the customer
  currently running on?`

Soft defaults (document and proceed):
- `deal_stage` absent → default "discovery".
- `timeline` absent → default "flexible".

Capture `budget_signal`, `customer_industry`, `competitive_context` from notes if present.

Do not call `generate_poc_plan` when the user only asked for diagram, BOM, JEP,
Terraform, WAF, or a generic explanation.

### Post-Action Review

Mandatory checks:
- `poc_options` present with three ranked options, or failed angles clearly visible in
  the trace.
- Each option has all required scoring and demo fields: `option_name`, `relevance_score`,
  `executability_hours`, `cost_effectiveness`, `security_highlights`, `wow_moment`,
  `demo_script_summary`, `oci_services`.
- Recommendation references customer's pain, timeline, budget signal, current platform,
  industry, or competitive context.
- Top recommendation is buildable in stated `executability_hours`.
- Selected POC can feed diagram, BOM, JEP, Terraform, and presentation without changing
  the proof point.

Decision: all checks pass → approve. Missing pain/current platform → surface clarification.
Weak or generic option names → iterate. Recommendation doesn't cite customer input →
iterate with corrected rationale.

### Handoff / Coordination

- `can_hand_off_to`: `diagram_for_oci`, `oci_bom_expert`, `jep_writer`, `terraform_for_oci`.
- `suggested_next_hat`: `diagram_for_oci`.
- `parallel_with`: `infra_tech_research`.
- Handoff message: "POC plan selected. Suggest diagram and BOM next, then JEP and Terraform
  after the customer confirms the build path."
- Triggers: "POC plan is selected", "customer confirms recommended POC".

### Current State Notes

- The `PocStrategistHandler` makes 3 parallel `asyncio.gather()` calls to the sub-agent
  with different angle instructions appended to the task. The hat declares `parallel_with:
  ["infra_tech_research"]` but the parallelism is actually *within* the tool call (3
  sub-agent instances), not between tools. This is correct but worth distinguishing.
- The post-action "all options present or failed angles visible in trace" check is
  forgiving. If 2 of 3 sub-agent calls succeed, `PocStrategistHandler` still returns
  partial results without failing the tool. The hat should make this more explicit: 3
  options is the target, but 2 with trace evidence is acceptable; 1 or 0 is a hard retry.
- The `action="confirm"` fan-out path (returning `ToolResult(status="parallel", ...)` for
  all 5 artifacts) is implemented in `PocStrategistHandler` but not described in the hat.
  The hat only describes the exploration path. The confirmation + fan-out behaviour should
  be documented here so future reviewers understand the full lifecycle.
- Version 1.0 — newly created in the p56 cycle. The pre-action `NEEDS_CLARIFICATION` gates
  are the primary strength. The "do not activate for diagram/BOM/JEP/Terraform requests"
  rule is an important guard against spurious activation.
- `relevance_score` is 1–10, `executability_hours` is an integer, and `security_highlights`
  must be real OCI controls. These constraints are in the quality bar but are enforced only
  by the LLM post-review — no schema validation in the handler.

---

## OCI Presentation Writer

**Intent:** Expert lens for POC PowerPoint deck generation using Oracle's OCI design
standards. It gates the tool on confirmed `poc_recommendation` and `customer_name`, runs
in the POC fan-out in parallel with diagram/BOM/JEP/Terraform, and reviews the returned
`.pptx` artifact for 7-slide completeness, Oracle icon usage, customer branding, and
cost/timeline accuracy.

| Field | Value |
|---|---|
| Version | 1.0 |
| Paired tool | `generate_presentation` |
| Activation mode | Automatic via `requires_hat="oci_presentation_writer"` on tool registration |
| File | `agent/hats/oci_presentation_writer.md` |

### Activates When

- User asks for a PowerPoint, deck, slides, presentation, or POC kit.
- POC confirmation fan-out includes `generate_presentation`.

(Only 2 activation triggers — the narrowest activation surface of any hat, intentional.)

### Memory Focus

Priority fields: `poc_recommendation`, `customer_name`, `bom_summary`, `jep_phases`,
`pain_statement`.

Summary style: presentation-oriented. Focuses exclusively on confirmed POC context
rather than the full engagement history.

### Pre-Action Checklist

- `poc_recommendation` absent → `NEEDS_CLARIFICATION: No POC has been planned yet. Run
  generate_poc_plan first.`
- `customer_name` absent → `NEEDS_CLARIFICATION: What is the customer's name?`
- BOM summary and JEP phases absent → mark those slides as pending rather than inventing
  values.
- Verify service names are official OCI names.

### Post-Action Review

Mandatory checks:
- PPTX bytes non-empty and stored directly (not base64 at rest).
- Exactly 7 slides generated.
- Oracle red accent and OCI terminology present.
- Artifact key follows `presentation/{customer_id}/vN.pptx` pattern.

Quality bar: customer name on title slide; POC name and pain statement clear; OCI icon
stencil shapes on architecture slide when toolkit available; cost slide includes BOM
summary or marks estimate as pending; timeline slide includes ordered JEP phases; next
steps are action-oriented and demo-ready.

### Handoff / Coordination

- `can_hand_off_to`: `oci_poc_strategist`, `diagram_for_oci`, `oci_bom_expert`,
  `jep_writer`, `terraform_for_oci`.
- `suggested_next_hat`: null (presentation is a terminal artifact in the fan-out).
- `parallel_with`: `generate_diagram`, `generate_bom`, `generate_jep`,
  `generate_terraform` (runs in the POC fan-out).

### Current State Notes

- The presentation sub-agent (`sub_agents/presentation/server.py`) does NOT call an LLM.
  It reads the engagement context from memory fields and calls `render_oci_powerpoint.render()`
  directly. The hat's pre/post-action LLM calls are therefore reviewing the inputs and
  output of a deterministic renderer, not an LLM. This is fine but means the post-action
  cannot catch reasoning errors — only structural completeness errors.
- The hat references "Oracle red accent" as a branding check, but the actual Oracle OCI
  stencil colour scheme uses Oracle Red (#C74634) applied to icon borders and title bars.
  "Oracle red accent" is vague — specifying the hex code or the toolkit slide master would
  make this check actionable.
- `bom_summary` and `jep_phases` are marked optional (mark slides as pending if absent).
  This is pragmatic for the fan-out where BOM/JEP may still be generating in parallel.
  However, if those parallel tools complete after the presentation starts, there is no
  mechanism to update the pending slides. The deck is effectively produced with incomplete
  cost/timeline data.
- Version 1.0 — newly created in the p56 cycle. The 2-trigger activation surface is
  deliberately narrow; the hat is intended to run in the fan-out, not as a standalone.
- Overlap with `oci_sales_deck`: see sales deck notes. The boundary should be stated
  explicitly: `oci_sales_deck` for pre-POC narrative decks; `oci_presentation_writer` for
  post-POC-selection technical POC kits.

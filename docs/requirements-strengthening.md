# Requirements — Strengthening Sub-Agents and Skills

**Date:** 2026-05-28
**Status:** DRAFT — requires review and sign-off before any implementation
**Branch:** `claude/explore-repo-Os53i`
**Scope:** All 10 sub-agents and 12 skills/hats

---

## Problem Statement

Grok's independent assessment (May 2026) rated the skill/hat layer at 4.5/10 and
sub-agents at 3.5/10. The combined diagnosis: we vibed our way into mediocrity.

Specific failures:

- Sub-agents re-ask for facts already in engagement_context.
- Output contracts exist on paper but are not enforced in code or post-action review.
- Hat pre/post-action thinking is mechanically checklist-driven, not domain-expert-driven.
- The diagram agent — the original system strength — regressed from deterministic to LLM-heavy.
- Revision intelligence is weak across BOM, diagram, and Terraform. Second calls often
  regenerate from scratch.
- The distinction between a senior OCI architect's *instinct* and a junior engineer's
  *checklist* is absent from every v1.0 hat and most v1.1 hats.

---

## Root Causes

### Root Cause 1 — Determinism was abandoned

The early diagram agent worked because hard rules governed layout: flat parent model, gateway
position by type, icon mapping from a fixed stencil table. Those rules are still in the hat
but have migrated from code constraints to LLM guidelines. An LLM guideline drifts; a code
constraint does not.

**Fix principle:** Any decision that has a correct answer independent of customer context
must be deterministic (code or exact schema). LLM judgment is reserved for decisions that
genuinely require reasoning.

### Root Cause 2 — Context passing is broken

Sub-agents are passed a `task` string and an `engagement_context` dict. The LLM inside the
sub-agent regularly ignores the context dict and asks for information the context already
contains. The handler never pre-extracts key fields before calling the sub-agent.

**Fix principle:** Every sub-agent handler MUST extract the relevant fields from
`engagement_context` and inject them into the `task` string as a structured block before
calling the LLM. The LLM must not be required to discover context — it must be handed it.

### Root Cause 3 — Hats encode structure, not expertise

A checklist of things to verify is not expertise. Expertise is knowing which specific things
go wrong with OCI architectures, why they go wrong, and what the correct OCI-specific fix
is — not "add encryption" but "enable OCI Vault managed keys on Block Volume via
`kms_key_id` in the `oci_core_volume` resource."

**Fix principle:** Every pre/post-action section of every hat MUST encode at least three
OCI-specific failure patterns with exact service names, resource types, or pricing
consequences. Generic cloud advice is prohibited.

### Root Cause 4 — Output contracts are honesty documents, not enforced contracts

Every sub-agent has a documented output contract. None of the handlers validate that the
returned payload matches the contract before passing it to the post-action review. The hat's
post-action check is the only validation, and it runs inside the same LLM call that may have
produced the artifact — a conflict of interest.

**Fix principle:** Every handler MUST perform schema validation (field presence + type checks)
on the sub-agent response before the result reaches the hat post-action. Structural failures
(missing artifact_key, wrong field types) must be caught in code, not by the LLM.

---

## Principles for This Requirements Document

1. **MUST / MUST NOT statements are testable.** Every MUST has a corresponding test that can
   verify it without human judgment.

2. **Domain knowledge requirements name OCI specifics.** "Use OCI encryption" is not a
   requirement. "System prompt MUST include the instruction: 'Use `kms_key_id` on
   `oci_core_volume`, `oci_core_boot_volume`, and `oci_objectstorage_bucket` resources; never
   reference Oracle-managed keys as an acceptable default for regulated data'" is a requirement.

3. **No requirement depends on LLM enforcement alone.** If a requirement is important enough
   to write, it must have a corresponding code-level check or a deterministic validation step.

4. **Revision is a first-class operation.** Every sub-agent and hat MUST have explicit
   revision behavior: what changes, what is preserved, how the previous artifact is passed.

5. **Context hydration is the handler's job.** The LLM inside the sub-agent is not
   responsible for parsing engagement_context. The Python handler extracts and injects the
   relevant fields.

---

## Sub-Agent Requirements

---

### Diagram Sub-Agent

**Purpose:** The Diagram sub-agent ingests written workload descriptions and Bill of
Materials context and delivers a customer-facing OCI architecture diagram in `.drawio`
format. It is the most visible artifact in the Archie system — customers judge the quality
of the entire engagement from the first diagram. The agent must use Oracle's official OCI
draw.io stencil library icons (from `OCI_Library.xml` v24.2), follow OCI Well-Architected
Framework topology standards (public/private/data subnet tiers, gateway placement, security
boundaries), and produce diagrams that match the standard reference architectures Oracle
publishes at https://docs.oracle.com/solutions/ and https://www.oracle.com/cloud/architecture-center/.
The output must be immediately usable by the customer — loadable in draw.io or diagrams.net,
elements independently draggable, and visually consistent with Oracle's customer-facing
architecture standards. Every BOM service must have a corresponding node; every node must
use an OCI-standard icon.

**Grok rating:** Regressed / Weak (was the original system strength)

**Root cause:** The early drawing agent had a deterministic pipeline: LayoutIntent JSON →
`intent_compiler.py` → `layout_engine.py` → `drawio_generator.py`. The LLM produced the
LayoutIntent spec; code did everything else. That pipeline still exists but the LLM's
LayoutIntent output has drifted — it now makes layout decisions that belong in code, and
the system prompt lacks the specificity to prevent it.

Secondary cause: The handler does not pre-validate the LayoutIntent JSON before passing
it to the pipeline, so malformed specs cause pipeline errors rather than clean rejections.

#### Handler Requirements (Python — `agent/tools/diagram.py`)

1. **MUST extract from `engagement_context`** before calling the sub-agent: `artifact_key`
   of the current diagram (if one exists), `components`, `topology`, `subnet_tiers`, and
   `instance_counts`. These MUST be injected into the task string, not left for the LLM to
   discover.

2. **MUST validate the LayoutIntent JSON schema** before passing to `intent_compiler`. At
   minimum: `nodes` is a non-empty array; every node has `oci_type`, `label`, `subnet_tier`;
   `subnet_tiers` is a non-empty array. If validation fails, the handler MUST return a
   structured error to Forge with the specific field that failed — not a generic exception.

3. **MUST enforce the revision constraint**: if `artifact_key` exists in context, the task
   string MUST include `[UPDATE REQUEST — PRESERVE ALL EXISTING NODES EXCEPT: ...]` and
   MUST NOT request a full regeneration. Full regeneration is only permitted when no
   `artifact_key` exists or when the user explicitly requests a full redraw.

4. **MUST pass `diagram_name` and `customer_id`** to the sub-agent on every call. These
   MUST NOT be inferred or defaulted inside the sub-agent.

5. **MUST verify `node_count` against the request**: after the diagram is generated, the
   handler MUST count the distinct OCI service types in the request and verify that
   `node_count >= that count`. If not, the handler MUST retry once with a correction before
   returning a partial result.

#### System Prompt Requirements

6. **MUST include the complete `oci_type` → stencil mapping table** for every service in
   `agent/oci_standards.py`. The LLM MUST NOT be required to know stencil names from
   training data. Any `oci_type` not in the mapping table MUST produce a
   `need_clarification` response — never a fabricated stencil name.

7. **Gateway placement MUST be specified as a deterministic rule, not a guideline:**
   - IGW: `x = vcn_left - icon_w/2` — always left of VCN boundary.
   - NAT: `x = vcn_left - icon_w/2`, `y = igw_y + icon_h + gap` — left, below IGW.
   - DRG: `x = vcn_left - icon_w/2`, below NAT — left edge.
   - SGW: `x = vcn_right - icon_w/2` — always right of VCN boundary.
   - LPG: `x = vcn_right - icon_w/2`, below SGW — right edge.
   The prompt MUST state these as coordinate formulas, not prose guidance.

8. **Every draw.io cell MUST have `parent="1"`.** The system prompt MUST include:
   "CRITICAL: Set `parent` to `"1"` on every cell in the output XML. There are NO
   exceptions. Icons that appear visually inside subnet boxes are NOT XML children of
   those boxes. Nested parent values will corrupt the diagram."

9. **Internet users MUST sit outside the region/VCN boundary.** The prompt MUST specify:
   "Place the Internet user node at coordinates outside and to the left of the outermost
   region box. The region box must fully enclose all VCN, subnet, and service nodes."

10. **AI/ML service placement MUST be explicit.** If the task string contains any of:
    `generative_ai`, `ai_service`, `data_science`, `opensearch`, `llm`, `rag`, `vector`,
    the system prompt MUST include: "You MUST include nodes for the AI/ML services named
    above. Use these `oci_type` values: generative_ai → `generativeai`,
    OpenSearch → `opensearch`, Data Science → `datasciencenotebook`. Failure to include
    them when requested is a critical error."

11. **Subnet tier classification MUST be exhaustive.** Every node MUST be assigned to one
    of: `Public`, `Private`, `Data`, `Management`. The prompt MUST prohibit any tier label
    not in this list. "Generic" and "Other" are not valid tier values.

12. **Multi-AD layouts MUST replicate correctly.** If `ha_dr_mode` contains
    `active_active` or `multi_ad`, the prompt MUST instruct: "Replicate the Private and
    Data subnet tiers into a second AD column. Each AD column gets its own labeled AD box.
    The Load Balancer spans both ADs and is shown once in the Public subnet."

#### Domain Knowledge Requirements

13. The system prompt MUST encode these specific failure patterns:
    - **Missing VCN boundary**: "If you do not emit a VCN container rectangle wrapping all
      subnet boxes, the diagram is invalid. The VCN box is always required."
    - **DB in Public subnet**: "Database nodes (ADB, DB System, MySQL, NoSQL) MUST be in
      the Data tier. A database node in the Public subnet is a security violation and will
      be rejected."
    - **LB in Private subnet**: "The Load Balancer MUST be in the Public subnet. A load
      balancer in the Private subnet cannot receive internet traffic and is architecturally
      incorrect."
    - **Missing NSG boundary**: "Every architecture MUST include at least one NSG boundary
      indicator. NSGs are not optional in OCI production deployments."

#### Definition of Done

- Handler validates LayoutIntent schema before pipeline in code.
- System prompt contains the complete `oci_type` → stencil table.
- Three consecutive diagram generations for a standard 3-tier web app produce the same
  node count, gateway positions, and parent model without correction.
- An update request (add one service) results in a new diagram with all original nodes
  preserved plus the new service — no other nodes removed.
- A request containing "GenAI" results in a diagram with a `generativeai` node in every
  run.

---

### BOM Sub-Agent

**Purpose:** The BOM sub-agent ingests workload descriptions, sizing parameters, and
engagement context and delivers an itemized Oracle Cloud Infrastructure Bill of Materials
in XLSX format. The BOM is the financial backbone of every customer conversation — SAs
use it to anchor budget discussions, justify shape selection, and produce the cost slide
in customer presentations. The agent must source all unit prices from the live OCI price
list API (https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/), use only
verified OCI part numbers (B-prefixed SKUs), and compute monthly totals arithmetically
— never estimate. The output must be immediately presentable to a customer CFO or
procurement team: clearly structured XLSX with line items, assumptions documented,
and a download link the SA can share without further editing.

**Grok rating:** Poor

**Root cause:** Two separate failures. First, the sub-agent re-asks for shape, OCPU, region,
and HA mode even when they appear in `engagement_context` — context hydration is broken.
Second, revision logic treats every correction as a full regeneration rather than applying
a surgical delta to the previous line items.

#### Handler Requirements (Python — `agent/tools/bom.py`)

1. **MUST extract and inject** into the task string before calling the sub-agent:
   `compute_shape`, `ocpu_count`, `memory_gb`, `region`, `ha_mode`, `budget`,
   `storage_requirements`, `workloads`, and `license_type` from `engagement_context`. If a
   field is present, it MUST appear in a `[CONFIRMED CONTEXT]` block at the top of the
   task. The sub-agent MUST NOT ask for any field that appears in this block.

2. **MUST pass the previous BOM payload** (if one exists) to the sub-agent on revision
   calls. The task string MUST include `[PREVIOUS BOM — PRESERVE UNLESS CHANGING: <json>]`.
   The sub-agent MUST NOT regenerate unchanged line items.

3. **MUST validate the returned payload** before returning to Forge:
   - `bom_payload.line_items` is a non-empty array.
   - Every `line_item` has `sku`, `quantity` > 0, `unit_price` > 0, `monthly_cost`.
   - `monthly_total` equals the arithmetic sum of `quantity × unit_price × 730` for all
     line items to within 0.5%. If the math fails, the handler MUST retry once with a
     correction prompt naming the specific discrepancy.
   - `artifact_key` is present (XLSX was persisted).

4. **MUST include the pricing source** in the result: `"prices_from": "live_api"` or
   `"prices_from": "fallback_cache"`. The live API URL and cache timestamp MUST be available
   to the post-action review.

5. **MUST validate SKU format**: every SKU MUST match `/^B\d{4,6}$/`. A SKU that does not
   match this pattern MUST be flagged as `sku_unverified` in the line item — not silently
   accepted and not silently rejected.

#### System Prompt Requirements

6. **MUST lead with the confirmed context block**: "Read the `[CONFIRMED CONTEXT]` block at
   the top of this task. Do NOT ask for any field listed there. Do NOT ask for shape, OCPU,
   region, or HA mode — these have been confirmed. If a required field is absent from the
   context block, state which specific field is missing and use the default."

7. **MUST specify the shape selection hierarchy as an exclusive decision tree**, not
   a guideline:
   - Start: Is a shape explicitly named in context? → Use it.
   - Else: Is "Ampere" or "A1" named? → A1.Flex.
   - Else: Is "Intel" or "X9" named? → X9.
   - Else: Is "GPU" named? → Request confirmation before proceeding.
   - Else: Is "E6" named? → Use E6.Flex only if customer explicitly named it.
   - Else: → E5.Flex (AMD general-purpose default).
   "Do NOT select E6.Flex as a default. It is not a default shape."

8. **MUST specify compute line item format**: "Compute shapes MUST produce exactly two line
   items per shape instance type: one OCPU row and one memory row. Never combine them into
   a single 'instance' line item. The OCPU SKU for E5.Flex is B97384; the memory SKU is
   B97385. These are the only acceptable E5.Flex SKUs."

9. **MUST specify managed service billing rules**:
   - OKE control plane: free, MUST NOT appear as a line item.
   - Autonomous Database: charge ECPU (B99060) per hour + storage per GB. MUST ask BYOL
     status before pricing DB Cloud Service.
   - FastConnect: charge port hours at the committed speed, not per-GB transfer.
   - OCI Load Balancer: charge bandwidth (flexible shape, minimum 10 Mbps).

10. **Revision MUST be additive.** "If a `[PREVIOUS BOM]` block is present, modify ONLY the
    line items the user explicitly asked to change. Copy all other line items unchanged,
    preserving their SKU, quantity, unit_price, and notes. Recalculate `monthly_total` after
    applying changes."

#### Domain Knowledge Requirements

11. The system prompt MUST encode these specific pricing instincts:
    - **HA doubles compute**: "Active-active across ADs doubles the OCPU quantity and memory
      quantity for every compute shape. If `ha_mode = active_active`, multiply node count
      by 2 before generating line items."
    - **Storage tier selection**: "Block Volume Balanced tier = 10 VPU/GB. Higher Performance
      = 20 VPU/GB. Balanced is the default. Archive Object Storage is 0.0026/GB/month vs
      0.0255/GB/month for Standard — a 10× price difference. Never default to Standard for
      data that is accessed infrequently."
    - **GPU cost anchoring**: "BM.GPU4.8 (8× NVIDIA A100) costs ~$32/OCPU-equivalent/hour.
      At 730 hours/month a single node is ~$23,000/month. Always state this before confirming
      a GPU line item. Never include a GPU line item without explicit customer acknowledgement."
    - **BYOL Oracle DB savings**: "BYOL Database Cloud Service saves ~50% vs Licence
      Included. This is always worth asking. Never assume Licence Included for customers
      who currently run Oracle DB on-prem — they almost certainly have BYOL licences."

#### Definition of Done

- Handler injects `[CONFIRMED CONTEXT]` block on every call.
- Handler validates `monthly_total` arithmetic in code; discrepancy > 0.5% triggers a retry.
- A BOM revision call (change one line item) returns a BOM with all other line items
  identical to the previous version — verified by field comparison in a test.
- A call with E5.Flex context produces B97384/B97385 SKUs. A call with E6.Flex explicitly
  named produces B111129/B111130. A call with no shape named produces E5.Flex defaults.
- GPU line items only appear after explicit customer acknowledgement — tests mock the
  confirmation flow.

---

### POV Sub-Agent

**Purpose:** The POV sub-agent ingests customer discovery notes, engagement context, and
meeting intelligence and delivers an internal Oracle Point of View document. A POV is a
strategic alignment document used by Oracle SAs, account executives, and sales leadership
to build a shared vision for the customer's OCI journey. It is NOT a customer-facing
document — it is Oracle-internal. The document must be structured in three sections:
Internal Press Release (a future-state success narrative), Customer FAQ (objection handling
and proof points), and Internal Oracle Questions (what the Oracle team must resolve to close
the deal). The POV must name specific OCI services (not generic "Oracle cloud"), include
measurable success outcomes, position OCI competitively against the customer's current
environment, and incorporate industry-specific context. SAs use the POV to prepare for
executive customer meetings and to align internal teams on pursuit strategy.

**Root cause:** Context hydration failure (asks discovery questions when engagement_context
already has the answers); output sections are structurally present but generically written.

#### Handler Requirements

1. **MUST pre-extract from `engagement_context`** before calling: `customer_name`,
   `customer_industry`, `customer_challenge`, `current_state`, `target_workloads`,
   `success_criteria`, `competitive_context`. MUST inject as `[CUSTOMER CONTEXT]` block.

2. **MUST detect discovery mode correctly**: if `customer_name` is absent OR
   `customer_challenge` is absent OR `target_workloads` is absent from context, the handler
   MUST return `status: "need_clarification"` with the specific missing field — before
   calling the sub-agent at all. Do not pay for a sub-agent call to discover that the
   context is insufficient.

3. **MUST validate the returned document** has all three section headings present:
   "Internal Press Release", "Customer FAQ", "Internal Oracle Questions". If any is absent,
   the handler MUST retry once with the specific missing section named.

4. **MUST save the document** and return `doc_key` (not `artifact_key`). The post-action
   hat checks for `doc_key`. These MUST match.

#### System Prompt Requirements

5. **Discovery mode MUST be conditional.** "If a `[CUSTOMER CONTEXT]` block is present at
   the top of this task with `customer_name`, `customer_challenge`, and `target_workloads`
   all populated, DO NOT ask discovery questions. Proceed directly to writing the POV using
   the provided context."

6. **Press Release section MUST include:**
   - Future-state headline (12–18 months from now)
   - Oracle GVP quote (fictional but plausible, specific to the workload)
   - Customer CTO and CEO/COO quotes (specific to their industry pain)
   - At least 2 named OCI services (not "Oracle's cloud platform")
   - At least 2 measurable outcomes with percentages, dollar amounts, or latency numbers.

7. **Customer FAQ MUST include exactly 5 questions** covering:
   - The customer's primary challenge and how OCI addresses it specifically.
   - Why OCI vs. their current environment or alternative (AWS/Azure/on-prem).
   - Migration or adoption scope — what moves first.
   - What success looks like in 90 days.
   - The next concrete step the customer and Oracle will take.

8. **Internal Oracle Questions MUST include exactly 5 questions** covering:
   - Technical requirements and dependencies not yet confirmed.
   - Oracle engagement model (SA, CE, ACS, Partners, ISV).
   - Timeline and fiscal/executive events.
   - Strategic positioning vs. competition.
   - Risks and blockers the Oracle team must resolve before deal closure.

9. **Industry-specific context MUST be woven in**, not added as a paragraph. For each
   vertical the prompt MUST encode:
   - Financial Services: "Cite Oracle FSGBU, MAA for high availability, OCI GovCloud for
     regulated workloads. Position vs. AWS for Oracle DB licensing cost."
   - Healthcare: "Cite Oracle Health (Cerner), HIPAA BAA availability on OCI, PHI data
     residency controls via OCI security zones."
   - Retail/CPG: "Cite Oracle Retail, demand forecasting on OCI Data Platform, seasonal
     scaling with OCI Autoscaling."
   - Manufacturing: "Cite JD Edwards or E-Business Suite on OCI Dedicated Region, IoT
     integration with OCI Streaming."
   - Government: "Cite FedRAMP authorization, OCI Government Cloud, data sovereignty via
     dedicated region."

#### Domain Knowledge Requirements

10. The system prompt MUST encode these specific POV failure patterns:
    - **OCI service vagueness**: "Writing 'Oracle's database cloud' is a rejection-level
      failure. You MUST write 'Oracle Autonomous Database 23ai on Exadata Cloud Infrastructure'."
    - **Generic competition**: "Writing 'unlike other clouds' is a rejection-level failure.
      Name the competitor: 'Unlike AWS RDS, OCI Autonomous Database includes...' or 'Unlike
      Azure SQL MI, OCI Exadata Cloud@Customer provides...'"
    - **Vague success criteria**: "'Improved performance' is a rejection-level failure. Write
      'sub-100ms P99 query latency at 2,000 concurrent connections, measured in the Phase 3
      load test.'"

#### Definition of Done

- Handler returns `need_clarification` for a call with empty context — no sub-agent cost
  incurred.
- Handler detects all three section headings in code; missing heading triggers a code-level
  retry.
- A POV generated for a financial services customer contains "FSGBU" or "MAA" or
  "Oracle RAC" — verified by substring search in test.
- Output `doc_key` matches the format `pov/{customer_id}/v{N}.md`.

---

### JEP Sub-Agent

**Purpose:** The JEP sub-agent ingests POC scope, customer context, success criteria, and
kickoff question answers and delivers a Joint Execution Plan — the formal document that
governs an Oracle-customer POC engagement. The JEP is a bilateral commitment document:
it names what Oracle and the customer will each build, test, and decide within a bounded
timeframe. It must be specific enough that both teams can execute without daily direction
from the SA. The output must include a phased execution plan (Assessment → Build → Validate),
SMART success criteria with numeric thresholds, a risk registry with customer-specific risks
and mitigations, explicit resource commitments from both Oracle and the customer, and a
go/no-go decision framework. SAs use the JEP to kick off a POC, align customer stakeholders,
and document the criteria that define a successful POC outcome.

**Root cause:** Kickoff gate fires even when kickoff answers are in engagement_context. Risk
registry uses boilerplate OCI risks rather than customer-specific risks. `doc_key` vs
`artifact_key` naming inconsistency between sub-agent output and hat checks.

#### Handler Requirements

1. **MUST extract from `engagement_context` before calling**: `poc_scope`, `poc_workloads`,
   `success_criteria`, `timeline`, `stakeholders`, `kickoff_answers`. If `kickoff_answers`
   is populated, MUST inject as `[KICKOFF ANSWERS — DO NOT RE-ASK]` block.

2. **Kickoff gate MUST be evaluated in the handler**, not the sub-agent. If any of
   `customer_name`, `poc_scope`, `poc_workloads` are absent from context, handler MUST
   return `status: "need_kickoff"` with the specific missing fields — before calling the
   sub-agent.

3. **MUST validate returned document** for all 9 required sections. If any section is absent,
   handler MUST retry once with the specific missing section named.

4. **MUST return `doc_key`**, not `artifact_key`. The hat post-action checks `doc_key` and
   the critic validates `doc_key`. These MUST be consistent.

5. **MUST validate `success_criteria_count >= 3`** in the returned payload. If fewer than 3,
   handler MUST retry once with the instruction to add criteria until the count is met.

#### System Prompt Requirements

6. **Kickoff gate MUST be conditional.** "If a `[KICKOFF ANSWERS]` block is present, DO NOT
   ask the 7 kickoff questions. Use the answers provided to generate the JEP directly."

7. **Three phases MUST be present with exact names:**
   - "Phase 1 — Assessment (Weeks 1–2)"
   - "Phase 2 — Build (Weeks 3–N)"
   - "Phase 3 — Validate (Final 2 weeks)"
   Any phase with a different name or missing a week range MUST be rejected by the handler.

8. **Success criteria MUST be SMART.** The prompt MUST include these canonical
   anti-patterns and prohibit them:
   - PROHIBITED: "Improved performance" → REQUIRED: "< 100ms P99 query latency at 500 RPS"
   - PROHIBITED: "Cost savings" → REQUIRED: "≥ 30% reduction in Oracle DB licensing cost vs
     current on-prem spend"
   - PROHIBITED: "Successful migration" → REQUIRED: "All 12 application schemas migrated and
     validated against baseline with zero data loss by end of Week 10"

9. **Risk registry MUST include customer-specific risks.** The prompt MUST say: "The risk
   registry MUST include risks specific to THIS customer's platform, network, and timeline.
   Risks may NOT be generic OCI POC boilerplate. If the customer is migrating Oracle DB,
   include risks about data volume, DBA availability, and network bandwidth. If the customer
   is on AWS, include risks about credential management and VPN/DRG setup."

10. **Resource commitments MUST be explicit.** "List Oracle SA name (or 'TBD'), Oracle CE
    name (or 'TBD'), customer DBA name (or 'TBD'), and their weekly hour commitments. An
    unstaffed role MUST be flagged as 'RISK: Unfilled — blocks Phase 2 start.'"

#### Domain Knowledge Requirements

11. The system prompt MUST encode these JEP-specific failure patterns:
    - **Scope creep hidden in Phase 2**: "Phase 2 must list only the services and workloads
      named in the Scope section. If you add a service in Phase 2 that was not in Scope, that
      is scope creep and the document is inconsistent."
    - **Missing go/no-go decision in Phase 3**: "Phase 3 MUST end with a go/no-go decision
      framework: which success criteria must pass, who signs off, and what happens if criteria
      are not met."
    - **Optimistic timeline**: "An 8-week timeline with a 6-week build phase and no buffer
      is unrealistic. Assume 10–15% schedule slip and add buffer weeks to Phase 2."

#### Definition of Done

- Handler evaluates kickoff gate in code; no sub-agent call occurs if `customer_name` is
  absent.
- Handler validates all 9 section headings in code; missing section triggers code-level
  retry.
- Handler validates `success_criteria_count >= 3` in code.
- Output key is always `doc_key`, never `artifact_key`.
- A JEP generated with "improve performance" as a success criterion triggers a retry that
  produces a SMART criterion with a numeric threshold.

---

### WAF Sub-Agent

**Purpose:** The WAF sub-agent ingests architecture descriptions, diagrams, and compliance
context and delivers an OCI Well-Architected Framework review — a structured assessment of
the architecture against all six OCI WAF pillars: Security, Reliability, Performance
Efficiency, Cost Optimisation, Operational Excellence, and Continuous Improvement. The
review is used by SAs to identify and remediate architecture risks before a customer
presentation or POC, and by customers to demonstrate architectural maturity to their own
security and compliance teams. Every finding must cite an OCI-specific service, resource
type, or configuration — not generic cloud advice. The review must include a maturity score
(1–5) per pillar, severity-rated findings (P1/P2/P3), and — when the customer operates in
a regulated industry — explicit mapping to compliance control frameworks (SOC 2, PCI DSS,
ISO 27001, FedRAMP). The output must be a saved Markdown document the SA can attach to a
customer presentation or share with the customer's security team.

**Root cause:** The single largest structural bug in the system — the post-action mandatory
check says "5 pillars" when the quality bar and OCI WAF Framework both require 6. The sixth
pillar (Continuous Improvement) is documented in the quality bar but consistently missing
from generated reviews because the post-action check does not enforce it.

#### Handler Requirements

1. **MUST extract from `engagement_context`** before calling: `topology`, `public_exposure`,
   `compliance_requirements`, `compliance_framework`, `data_classification`, `rto_rpo`.
   MUST inject as `[ARCHITECTURE CONTEXT]` block.

2. **MUST validate all 6 pillar keys** in the returned payload in code:
   `Security`, `Reliability`, `Performance Efficiency`, `Cost Optimisation`,
   `Operational Excellence`, `Continuous Improvement`. If any key is missing, handler MUST
   retry once with the specific missing pillar named.

3. **MUST validate maturity scores** are integers in range [1, 5] for all 6 pillars.
   A maturity score of 0 or null is a handler-level error, not a post-action concern.

4. **MUST validate P1 presence** for architectures with public-facing services: if
   `public_exposure` in context includes "LB", "WAF", "API Gateway", or "internet", and
   the returned `Security.findings` contains zero P1 findings, the handler MUST retry once.
   A public-facing architecture with no P1 security findings is a red flag, not a pass.

5. **MUST validate `artifact_key` presence** before returning. A WAF review without a saved
   artifact key MUST be retried, not surfaced as a partial success.

#### System Prompt Requirements

6. **MUST specify all 6 pillars with their mandatory content.** The post-action check now
   enforces 6; the system prompt must match:
   - Security: public ingress controls (OCI WAF policy, NSG), IAM separation, OCI Vault KMS,
     encryption at rest (Block Volume, Object Storage), TLS, Bastion Service for admin.
   - Reliability: multi-AD distribution, OCI Load Balancer health checks, backup schedules,
     stated RTO/RPO.
   - Performance Efficiency: OCI shape right-sizing, autoscaling policy (OCI Autoscaling
     or OKE HPA), Block Volume tier selection.
   - Cost Optimisation: committed use discount opportunity (BYOL, Reserved Capacity), Object
     Storage lifecycle policy, right-sizing signal from OCI Monitoring.
   - Operational Excellence: OCI Logging enabled, OCI Monitoring alarms (CPU > 85%, disk
     > 80%), OCI Events + Notifications for state changes.
   - Continuous Improvement: OCI DevOps or GitHub Actions pipeline, OCI Functions for
     automation, feedback loop to BOM/diagram if architecture changes.

7. **Maturity scoring MUST be calibrated to OCI defaults.** The prompt MUST specify:
   - Score 1: No controls in place. No OCI WAF policy, no KMS encryption, no logging.
   - Score 2: Some controls. WAF policy exists but no tuning; Oracle-managed keys; logging
     enabled but no alarms.
   - Score 3: Standard OCI controls. Custom WAF rules, KMS with rotation, OCI Monitoring
     alarms on critical metrics, multi-AD compute.
   - Score 4: Measured. SIEM integration via OCI Logging + SIEM connector, SLA-tracked
     backup compliance, automated cost anomaly alerts.
   - Score 5: Optimised. Automated remediation via OCI Functions, Security Advisor policies
     enforced, continuous benchmark against OCI Landing Zone standards.

8. **Compliance mapping MUST include control IDs.** The prompt MUST specify:
   - SOC 2: Map findings to CC6.1, CC6.2, CC6.6, CC6.7, CC7.1, CC7.2 (security criteria).
   - PCI DSS: Map to Req 1 (network segmentation), Req 3 (encryption at rest), Req 7 (access
     control), Req 10 (audit logging).
   - ISO 27001: Map to Annex A controls A.9 (access), A.10 (cryptography), A.12 (ops),
     A.13 (network), A.16 (incident).
   - FedRAMP Moderate: Map to AC-2, SC-8, AU-2, CM-2, IR-6 controls.

9. **OCI-specific evidence is required for every finding.** The prompt MUST prohibit:
   - PROHIBITED: "Use encryption" → REQUIRED: "Enable OCI Vault managed keys on
     `oci_core_volume` resources via `kms_key_id`"
   - PROHIBITED: "Monitor your instances" → REQUIRED: "Create OCI Monitoring alarm on
     `CpuUtilization > 85%` in namespace `oci_computeagent` with 5-minute evaluation window"
   - PROHIBITED: "Use network segmentation" → REQUIRED: "Replace security lists with NSGs;
     ensure `prohibit_public_ip_on_vnic = true` on all private subnet VNICs"

#### Domain Knowledge Requirements

10. The system prompt MUST encode these WAF-specific failure patterns:
    - **The WAF-less LB**: "Any architecture with an OCI Load Balancer in the Public subnet
      that lacks an OCI WAF policy is a P1 Security finding. This is the single most common
      OCI architecture security gap."
    - **Oracle-managed keys as a default**: "Oracle-managed keys are a starting point, not a
      security control. Any architecture processing regulated data (PII, PHI, PCI) without
      OCI Vault KMS key rotation configured is a P1 finding under every compliance framework."
    - **Missing Bastion Service for admin access**: "SSH/RDP to private compute instances
      must route through OCI Bastion Service. Direct port 22/3389 access from an SA's laptop
      is a P1 finding — not a P2 advisory."
    - **Single-AD for production SLA claims**: "A customer claiming 99.99% availability
      with a single-AD deployment has an arithmetic error. OCI's 99.99% SLA requires
      multi-AD deployment for compute. Calling this out is a core Reliability finding."

#### Definition of Done

- Handler validates all 6 pillar keys in code; missing pillar triggers code-level retry.
- Handler validates maturity scores are integers in [1, 5] in code.
- Handler validates P1 presence for public-facing architectures in code.
- A WAF review generated for a public LB architecture always contains at least one P1
  Security finding — verified by test with mock sub-agent returning a WAF-less LB design.
- Pillar count is 6 in every generated review, verified by JSON key count in test.

---

### Terraform Sub-Agent

**Purpose:** The Terraform sub-agent ingests architecture context, resource scope, and
infrastructure requirements and delivers a production-ready OCI Terraform bundle — a
five-file Infrastructure as Code package the customer's engineering team can `terraform init`,
`plan`, and `apply` against their OCI tenancy. The bundle must follow Oracle's OCI Terraform
best practices: OCI provider version >= 5.40.0, no hardcoded OCIDs, `locals` block for
common tags and naming, modular structure for non-trivial architectures, and OCI Object
Storage as the state backend. The output must be immediately deployable by a customer DevOps
engineer who has never seen the architecture before — `README.md` explains every prerequisite,
`terraform.tfvars.example` stubs every required variable, and all resources follow consistent
naming and tagging conventions. SAs use the Terraform bundle to give customers a working IaC
starting point for their POC or production deployment, reducing setup time from days to hours.

**Root cause:** File list inconsistency between quality bar (`README.md`) and post-action
(`provider.tf`). State backend block not verified. Module structure promised in quality bar
but not enforced in post-action. No check that `freeform_tags` is on every resource.

#### Handler Requirements

1. **MUST extract from `engagement_context`** before calling: `compartment_ocid` (or
   `compartment_id`), `region`, `resources`, `naming_conventions`, `tagging_requirements`,
   `terraform_scope`. MUST inject as `[TERRAFORM CONTEXT]` block.

2. **Canonical 5-file list is fixed.** The handler MUST validate these exact keys in the
   returned `files` dict: `main.tf`, `variables.tf`, `outputs.tf`,
   `terraform.tfvars.example`, `README.md`. There is NO `provider.tf` — the provider block
   lives in `main.tf`. Any reference to `provider.tf` as a separate file is incorrect and
   MUST be corrected in the hat.

3. **MUST validate no hardcoded OCIDs** in code: scan `main.tf`, `variables.tf`, and
   `outputs.tf` for any string matching `/ocid1\.[a-z]+\.[a-z]+\.[a-z-0-9]+\.[a-z0-9]+/`.
   If found, handler MUST retry once with the specific line numbers and literal values to
   replace with `var.*` references.

4. **MUST validate provider version** in `main.tf`: search for `hashicorp/oci` and verify
   the version constraint is `>= 5.40.0`. If absent or lower, handler MUST retry.

5. **MUST validate `locals` block presence** in `main.tf`: search for `locals {`. If absent,
   handler MUST retry.

6. **MUST validate `freeform_tags`** appears on every `resource` block: any `resource`
   block in `main.tf` without `freeform_tags = local.common_tags` MUST trigger a retry
   naming the specific resource type(s) missing it.

7. **MUST validate `artifact_key` presence** before returning.

#### System Prompt Requirements

8. **Canonical file structure MUST be specified exactly:**
   - `main.tf`: required_providers block (oci >= 5.40.0), backend block (http), locals block
     with `common_tags` and `name_prefix`, all resource definitions.
   - `variables.tf`: every variable has `type`, `description`. Sensitive variables have
     `sensitive = true`. No defaults for OCID variables.
   - `outputs.tf`: VCN OCID, all subnet OCIDs, compute instance OCIDs/IPs minimum.
   - `terraform.tfvars.example`: stub value for every non-sensitive variable.
   - `README.md`: Terraform version requirement (>= 1.5), OCI provider version, all required
     variables, terraform init/plan/apply commands.

9. **MUST specify locals block exactly:**
   ```hcl
   locals {
     common_tags = {
       Project     = var.project_name
       Environment = var.environment
       Owner       = var.owner_email
       ManagedBy   = "terraform"
     }
     name_prefix = "${var.project_name}-${var.environment}"
   }
   ```
   "Every resource MUST include `freeform_tags = local.common_tags`. This is not optional."

10. **State backend MUST be specified:** "Include this backend block in `main.tf`:
    ```hcl
    terraform {
      backend "http" {}
    }
    ```
    Add a comment explaining OCI Object Storage PAR configuration. If the customer
    explicitly accepts local state, document it and omit the backend block — but this
    requires explicit confirmation."

11. **Data source for AD discovery MUST be included:**
    ```hcl
    data "oci_identity_availability_domains" "ads" {
      compartment_id = var.tenancy_ocid
    }
    ```
    "Never hardcode AD names (e.g., 'TBzR:US-CHICAGO-1-AD-1'). Always reference
    `data.oci_identity_availability_domains.ads.availability_domains[0].name`."

12. **Security defaults MUST be encoded:**
    - Private subnets: `prohibit_public_ip_on_vnic = true` on all compute instances.
    - NSGs in preference to security lists wherever the resource supports NSGs.
    - Block Volume encryption: `kms_key_id = var.vault_key_id` on every volume resource.

13. **Module structure MUST be enforced for non-trivial architectures.** If the resource
    count > 5, the prompt MUST instruct: "Create modules: `modules/vcn/`, `modules/compute/`,
    `modules/database/` as subdirectories with their own `main.tf` and `variables.tf`. The
    root `main.tf` calls modules with `source = "./modules/vcn"`. A monolithic `main.tf`
    over 200 lines is not acceptable."

#### Domain Knowledge Requirements

14. The system prompt MUST encode these Terraform-specific failure patterns:
    - **Compartment OCID as tenancy root**: "NEVER use `var.tenancy_ocid` as a
      `compartment_id`. Always use a dedicated `var.compartment_id` that the customer sets
      to a non-root compartment."
    - **Hardcoded availability domain names**: "AD names like 'TBzR:US-CHICAGO-1-AD-1' are
      tenancy-specific. Hardcoding them causes the configuration to fail in any other
      tenancy. Always use the data source."
    - **Missing deletion protection on databases**: "ADB resources MUST include
      `is_auto_scaling_enabled = false` (for POC) and should include a note to enable
      termination protection in production."
    - **No count/for_each for identical resources**: "If the architecture has 3 app servers,
      use `count = var.app_server_count` with `var.app_server_count` defaulting to 3 — not
      3 separate resource blocks."

#### Definition of Done

- Handler validates 5 canonical file names in code.
- Handler scans for hardcoded OCIDs in code; found OCID triggers code-level retry.
- Handler validates provider version in code.
- Handler validates `locals` block presence in code.
- A Terraform bundle generated for a 3-tier architecture has `freeform_tags = local.common_tags` on every resource — verified by grep in test.
- No bundle contains a hardcoded AD name — verified by pattern search in test.

---

### Tech Research Sub-Agent

**Purpose:** The Tech Research sub-agent ingests a workload description, architecture
question, or technology comparison request and delivers a structured OCI technology
evaluation report. It is the "justify before you build" agent — it answers "which OCI
services should we use and why?" before the SA commits to a BOM or diagram. The report
must evaluate at least two concrete OCI architecture options with specific service names,
shapes, pros/cons, and rough cost estimates; recommend one option with a rationale tied
to the customer's workload; and provide sizing hints structured for direct use by the BOM
sub-agent. The agent must draw on Oracle's published reference architectures, OCI service
documentation, and OCI pricing to produce credible, specific recommendations. SAs use this
report when a customer asks "what's the best OCI approach for X?" and the answer isn't
immediately obvious from the BOM or diagram context alone.

**Root cause:** Port 8087 conflict with Terraform sub-agent (both claim the same port —
they cannot run simultaneously). Output is sometimes markdown-wrapped JSON (same issue as
poc_strategist). Sizing hints under-specified when the research question is vague.

#### Handler Requirements

1. **Port conflict MUST be resolved.** Tech research sub-agent MUST move to port 8086.
   Terraform stays at 8087. This is a hard prerequisite — the sub-agent cannot function
   in production on a shared port.

2. **MUST extract from `engagement_context`** before calling: `workload_description`,
   `workload_pattern`, `current_platform`, `compliance_requirements`, `gpu_requirements`,
   `region`, `budget_range`. MUST inject as `[RESEARCH CONTEXT]` block with:
   ```
   [RESEARCH CONTEXT]
   Workload: <workload_description>
   Current platform: <current_platform or "not stated">
   Region: <region or "us-chicago-1">
   Compliance: <compliance_requirements or "none stated">
   Budget cap: <budget_range or "not stated">
   [/RESEARCH CONTEXT]
   ```

3. **MUST apply `_extract_json()`** to the sub-agent response (same fix as poc_strategist).
   The LLM may return markdown-wrapped JSON; the handler MUST strip the wrapper before
   attempting to parse.

4. **MUST validate sizing_hints completeness** in code: the returned
   `recommendation.sizing_hints` MUST have all 5 fields: `compute_shape`, `total_ocpu`,
   `total_memory_gb`, `block_volume_gb`, `ha_mode`. If any field is missing, handler MUST
   retry once with the specific missing fields named.

5. **MUST validate `options_evaluated` has >= 2 entries** in code. One-option research
   is not research — it is a recommendation without justification.

6. **MUST validate `risk_register` has >= 3 entries** in code.

7. **MUST validate `artifact_key` presence** before returning.

#### System Prompt Requirements

8. **Workload pattern MUST be selected from the canonical list** before any other output:
   "Start your response by naming the workload pattern. Choose exactly one from:
   `3-tier web`, `microservices`, `ML inference`, `data platform`, `batch`,
   `lift-and-shift`, `RAG`, `hybrid connectivity`. If the pattern is ambiguous, name the
   two most likely candidates and pick the higher-risk one. Do not invent pattern names."

9. **≥2 options MUST be concrete.** "For each option, name: the specific OCI services,
   the compute shape with OCPU/memory counts, the estimated monthly cost (order of
   magnitude acceptable), 2 pros, 2 cons. Generic options ('use OCI database') are
   prohibited — options must include shape names (E5.Flex, A1.Flex, BM.GPU.A10) and
   service names (OKE, Autonomous DB, OCI Data Science)."

10. **Sizing hints MUST be directly usable by the BOM sub-agent.** "The
    `recommendation.sizing_hints` block MUST be structured so the BOM sub-agent can
    generate a BOM from it without asking any questions. Include: compute_shape (full OCI
    name), total_ocpu (integer), total_memory_gb (integer), block_volume_gb (integer),
    ha_mode ('single-AD' or 'active-active')."

11. **Risk register MUST be customer-platform-specific.** "Risks MUST reference the
    customer's current platform or workload. If the customer is on AWS, risk 1 is IAM
    policy translation. If the customer is on-prem Oracle DB, risk 1 is data volume and
    DBA availability. Generic risks like 'change management' are prohibited."

#### Definition of Done

- Tech research sub-agent runs on port 8086 without conflict.
- Handler validates sizing_hints 5-field completeness in code.
- Handler validates >= 2 options in code.
- Handler validates >= 3 risk_register entries in code.
- Handler applies `_extract_json()` before parsing.
- A research report generated for "migrate Oracle DB" contains "lift-and-shift" or
  "modernisation" as workload pattern — not a free-form description.

---

### Sales Deck Sub-Agent

**Purpose:** The Sales Deck sub-agent ingests customer context, existing engagement
artifacts (POV, BOM, diagram), and the customer's challenge and delivers a JSON slide
specification for a customer-facing OCI solution recommendation presentation. The deck
is used by SAs in pre-sales executive meetings to present Oracle's proposed solution
before a POC is underway. It must tell the customer's story — not Oracle's feature catalog.
Every slide must connect to the customer's stated problem or business goal. The 8-slide
default structure moves from customer challenge → OCI solution → architecture → cost →
why OCI → next steps. The output is a JSON spec (not a rendered `.pptx`) that can be
reviewed and edited before rendering. SAs use it to prepare for C-suite meetings, QBRs,
and solution review sessions where a polished, customer-named deck is expected.

**Root cause:** Output is a JSON slide spec, not a rendered `.pptx`. Users asking for a
deck expect a file they can open in PowerPoint. The hat/handler never explicitly declares
this distinction, causing user confusion. Secondary: artifact hydration from existing POV,
BOM, diagram artifacts is not implemented in the handler.

#### Handler Requirements

1. **MUST hydrate from existing artifacts before calling.** If `pov_artifact_key` exists
   in context, handler MUST fetch the POV document and extract the customer challenge,
   OCI solution narrative, and competitive positioning. These MUST be injected as
   `[POV CONTEXT]` block. Same for BOM artifact (extract `monthly_total` and top 3 SKUs)
   and diagram artifact (extract `artifact_key` for the architecture slide reference).

2. **MUST make the output format explicit in the task string**: "Your output is a JSON
   slide specification that will be saved and displayed to the SA. It is NOT a rendered
   PowerPoint file. The slide spec will be rendered in a subsequent step."

3. **MUST validate the returned `deck_payload`**:
   - `slides` array is present and length equals requested slide count.
   - No slide has an empty `title`, `presenter_notes`, or primary content field.
   - `customer_name` appears in slide 1 `title` or `subtitle`.
   - No `{{token}}` or `[INSERT]` placeholder text in any field.

4. **MUST save with key `deck/{customer_id}/v{N}.json`** and return `artifact_key`.

#### System Prompt Requirements

5. **Output format MUST be declared unambiguously**: "You are producing a JSON slide
   specification. Each slide is a JSON object with fields: `slide_number`, `layout`,
   `title`, `content` (array of strings), `presenter_notes`. The title is a complete
   declarative sentence — not a topic heading."

6. **8-slide structure MUST be exactly specified with the customer-outcome principle:**
   - Slide 1: Title. Customer name, date, SA name. No Oracle marketing language.
   - Slide 2: Challenge. The customer's specific problem. "Digital transformation" is
     prohibited. Name the actual pain: cost, performance, reliability, compliance, toil.
   - Slide 3: OCI Solution. Pattern name + key OCI services. Max 3 services featured.
   - Slide 4: Architecture. Diagram `artifact_key` embedded as reference. If no diagram
     exists, describe the topology in `presenter_notes`.
   - Slide 5: BOM Summary. Top 3 cost drivers, `monthly_total`. If no BOM exists, "PENDING
     — BOM in progress" — never invent a number.
   - Slide 6: Why OCI. 2–3 customer-specific differentiators. "Oracle has great support"
     is prohibited. "E5.Flex delivers 35% better price-performance than comparable AWS
     instance types for Oracle DB workloads" is required.
   - Slide 7: Next Steps. ≥2 milestones with dates or durations. At minimum: POC kickoff
     and executive review.
   - Slide 8: Appendix. Assumptions, open questions, pricing caveats.

7. **Presenter notes MUST be actionable**, not summaries of slide content. The prompt MUST
   specify: "Presenter notes tell the SA what to say, what objection to anticipate, and
   what proof point to cite. 'This slide shows the architecture' is not a presenter note.
   'Ask the customer CTO if this matches their understanding of the current network
   topology — the DRG placement is a common point of contention' is a presenter note."

#### Domain Knowledge Requirements

8. The system prompt MUST encode these deck-specific failure patterns:
   - **Oracle feature catalog slides**: "A slide titled 'OCI Capabilities' listing 20
     services is a failure. Every slide must be about this customer's problem or this
     customer's solution."
   - **Invented pricing**: "If a BOM artifact does not exist, write 'PENDING' for any cost
     figure. Never invent a monthly total. Invented numbers in a customer deck are a
     credibility risk."
   - **Generic Why-OCI content**: "Saying 'OCI has 99.9% SLA' is not differentiation. The
     correct approach: 'For Oracle Database workloads, OCI Exadata Cloud@Customer provides
     MAA architecture with 99.995% availability, which AWS RDS cannot match because AWS
     does not run Oracle Exadata.'"

#### Definition of Done

- Handler fetches and injects POV, BOM, diagram artifacts in code before calling sub-agent.
- Handler validates no `{{token}}` placeholder in any field in code.
- Handler validates `customer_name` appears in slide 1 in code.
- A deck generated with a BOM artifact shows the correct `monthly_total` from that artifact.
- A deck generated without a BOM artifact shows "PENDING" — never a fabricated number.

---

### POC Strategist Sub-Agent

**Purpose:** The POC Strategist sub-agent ingests customer discovery context — pain
statement, current platform, deal stage, timeline, budget signal, and competitive context
— and delivers a ranked set of three POC options, each evaluated against four criteria:
relevance to the customer's pain, executability within an SE's available time, cost
defensibility, and security story. It answers the question every SE faces before a POC:
"What should we build to close this deal?" The agent is called three times in parallel,
once per angle (migration/modernisation, performance/scale/AI, cost/TCO), and a synthesis
step produces the final ranked recommendation. Each option must include a specific wow
moment — a visible, memorable proof point a business or technical sponsor can remember
after the meeting. SAs use the POC Strategist output to align with their manager, prepare
the customer for the POC kickoff, and feed all subsequent artifact generation (diagram,
BOM, JEP, Terraform, presentation).

**Root cause:** Two issues from the p56f cycle. First, the LLM returns markdown-wrapped JSON;
`_extract_json()` was added but needs to be verified as sufficient. Second, the recommendation
rationale is generated by the `PocStrategistHandler` synthesis step (Python), not by the LLM
using customer context — so it is formulaic rather than insight-driven.

#### Handler Requirements

1. **MUST pass customer context per angle call.** Each of the 3 parallel sub-agent calls
   MUST include the full `[CUSTOMER CONTEXT]` block: `pain_statement`, `current_platform`,
   `deal_stage`, `timeline`, `budget_signal`, `customer_industry`, `competitive_context`.
   The angle instruction MUST be appended AFTER the context, not instead of it.

2. **MUST apply `_extract_json()` and validate JSON schema** for each of the 3 responses
   before synthesis. A response that cannot be parsed as valid JSON after extraction MUST
   be marked as a failed angle with the raw response logged. The handler MUST track how
   many angles failed.

3. **Partial success policy:**
   - 3/3 successful: proceed normally.
   - 2/3 successful: proceed with 2 options, log the failure, include a
     `"failed_angles": [{"angle": "...", "reason": "..."}]` field in the response.
   - 1/3 or 0/3 successful: MUST return an error result to Forge — not a single option.
     Log all raw responses.

4. **Recommendation rationale MUST be LLM-generated**, not Python-synthesized. The
   handler MUST pass all 3 option summaries back to the sub-agent in a synthesis call:
   "Given these 3 POC options and the customer context, write a 2–3 sentence recommendation
   rationale that cites specific customer inputs (pain, timeline, budget signal, competitive
   context). The rationale MUST name the customer's platform, timeline, or pain — not
   generic OCI value."

5. **Confirmation fan-out path MUST be documented** in the handler docstring: when
   `action="confirm"` is passed and `poc_recommendation` is in context, the handler returns
   `ToolResult(status="parallel", parallel_tools=[...])` for all 5 fan-out artifacts.

#### System Prompt Requirements

6. **Output format MUST be strict JSON with no markdown wrapper.** "Return ONLY a JSON
   object. Do NOT wrap in ```json or ``` markers. Do NOT include any prose before or after
   the JSON. The first character of your response MUST be `{` and the last MUST be `}`."

7. **One option per call.** "You are called once per POC angle. Return exactly ONE option
   object inside `poc_options[0]`. The handler will synthesize 3 angles into the final
   ranked list. Do not generate multiple options — generate the best option for your
   assigned angle."

8. **Option fields MUST be customer-specific:**
   - `option_name`: MUST include the customer's platform or workload (e.g., "Live Oracle
     RAC migration to ADB-Dedicated" not "Database POC").
   - `wow_moment`: MUST describe a specific visible result in a customer meeting (e.g., "Run
     a live query side-by-side showing 40% faster execution time than the on-prem RAC").
   - `demo_script_summary`: MUST be a 3-step script the SE can follow in a 20-minute demo.
   - `security_highlights`: MUST be real OCI controls (OCI Vault, Cloud Guard, OCI Security
     Zones — not "strong security").

9. **`relevance_score` MUST be justified.** After the score integer, the prompt MUST
   require: `"relevance_explanation": "Score 9 because the customer's stated pain is [X]
   and this option directly proves [Y] in [Z] hours."` This field is the difference between
   a score and an insight.

#### Domain Knowledge Requirements

10. The system prompt MUST encode POC-specific strategic instincts per angle:
    - **Migration/modernisation angle**: "Prioritise options that demonstrate zero-downtime
      cutover and managed service reduction in operational toil. The wow moment is the
      customer's own application running on OCI — not a demo app."
    - **Performance/scale/AI angle**: "Prioritise options that show something the customer
      cannot do on their current platform. GPU-accelerated inference, OCI GenAI API calls,
      OKE autoscaling to 10× load — things that are impossible or prohibitively expensive
      on the customer's current stack."
    - **Cost/TCO angle**: "Prioritise options where the OCI cost is measurable in the POC
      window. Use the BOM to calculate cost before and after. The wow moment is the CFO
      seeing the monthly bill drop by 35% with a specific dollar figure attached."

#### Definition of Done

- Handler passes `[CUSTOMER CONTEXT]` block to all 3 sub-agent calls.
- Handler validates JSON schema for all 3 responses in code.
- Partial success at 1/3 returns an error result, not a single option.
- Recommendation rationale is produced by a synthesis LLM call, not Python string
  concatenation — verified by reading the handler code.
- `relevance_explanation` field appears in every option in test output.

---

### Presentation Sub-Agent

**Purpose:** The Presentation sub-agent ingests a confirmed POC recommendation, customer
context, and existing artifacts (BOM summary, JEP phases, diagram reference) and delivers
a rendered 7-slide Oracle-standard `.pptx` POC kit the SA can hand to the customer at the
POC kickoff. It is the final tangible deliverable of the POC planning workflow. The deck
must use Oracle's official OCI Architecture Toolkit PPTX stencil for service icons, follow
Oracle's visual design standards (Oracle red, OCI typography, Oracle slide master), and be
immediately openable in Microsoft PowerPoint and Apple Keynote without errors. The 7-slide
structure covers: title, customer challenge, OCI architecture, key OCI services, cost
estimate, implementation plan, and next steps. Unlike the Sales Deck (which is a pre-POC
narrative deck), this is a post-selection technical POC kit — it describes exactly what
will be built, how much it costs, and when each phase delivers. The agent is a deterministic
renderer, not an LLM — it reads the engagement context and runs `render_oci_powerpoint.py`.

**Root cause:** The sub-agent does not call an LLM — it calls `render_oci_powerpoint.render()`
directly from the engagement_context. This is correct design but is undocumented. The Oracle
OCI toolkit PPTX stencil file path and the slide layout spec need to be hardened.

#### Handler Requirements

1. **MUST verify pre-conditions before calling the sub-agent:**
   - `poc_recommendation` in context: if absent, MUST return an error — not a blank deck.
   - `customer_name` in context: if absent, MUST return an error — the title slide will be
     wrong.
   - Oracle OCI toolkit PPTX file exists at `sub_agents/presentation/assets/`: if absent,
     MUST return an error explaining the toolkit file is missing.

2. **MUST pass a structured render spec** to the sub-agent rather than a free-form task
   string. The spec MUST include:
   ```json
   {
     "poc_name": "<from poc_recommendation>",
     "customer_name": "<from context>",
     "pain_statement": "<from context>",
     "oci_services": ["<from poc_recommendation.oci_services>"],
     "bom_summary": "<from bom artifact or 'PENDING'>",
     "jep_phases": ["<from jep artifact or []>"],
     "diagram_artifact_key": "<from diagram artifact or null>"
   }
   ```

3. **MUST validate returned bytes are non-empty** (len > 0) before saving. An empty bytes
   object MUST be treated as a generation failure.

4. **MUST validate the saved file opens as a valid ZIP archive** (`.pptx` files are ZIP).
   If `zipfile.is_zipfile(bytes)` returns False, MUST retry once.

5. **MUST save with key `presentation/{customer_id}/v{N}.pptx`**. The key MUST end in
   `.pptx` — not `.json`, not `.xml`.

6. **Content-Type in `/download` handler MUST be:**
   `application/vnd.openxmlformats-officedocument.presentationml.presentation`
   for any key ending in `.pptx`.

#### System Prompt Requirements (Render Script — not LLM)

7. **`render_oci_powerpoint.py` MUST enforce 7 slides exactly.** If the render function
   produces fewer than 7 slides, it MUST raise a `ValueError` with the missing slide
   numbers — not silently produce an incomplete deck.

8. **BOM and JEP pending handling MUST be deterministic:** if `bom_summary` is empty
   or null, slide 5 (cost) MUST render with text "BOM Pending — Estimated Cost TBD" in
   the cost cell. If `jep_phases` is empty, slide 6 (timeline) MUST render with
   "Implementation Plan Pending — JEP in Progress."

9. **OCI service icon resolution MUST fail loudly.** `resolve_oci_powerpoint_icon.py`
   MUST raise a `ValueError` for any service name not found in the toolkit. The caller
   MUST catch this and substitute a standard OCI compute icon + service name label rather
   than crashing or silently omitting the service.

#### Definition of Done

- Handler validates `poc_recommendation` and `customer_name` in code before calling.
- Handler validates the returned bytes as a valid ZIP in code.
- Handler retries once on empty bytes or invalid ZIP.
- Slide count is exactly 7 — enforced in render script with `ValueError`.
- BOM pending and JEP pending slides render with the specified placeholder text.
- `/download` returns correct Content-Type for `.pptx` keys.

---

## Skill / Hat Requirements

---

### Critic

**Purpose:** The Critic skill is a second-pass quality gate that activates after any
critique-enabled tool returns a result and the manager's expert post-review has approved
it. Its role is to validate the structural and mathematical correctness of tool outputs
against a per-tool schema — independently of the manager who just reviewed the same output.
The Critic catches failures that the manager's expert LLM call might rationalise away:
missing artifact keys, arithmetic errors in BOM totals, fabricated SKUs, wrong node counts,
invalid Terraform. It silently issues precise correction prompts and triggers re-calls up to
three times before escalating to the customer. It does not produce content — it only approves
or rejects, and every rejection names a specific field, expected value, and actual value.

**Root cause:** No validation schema for `generate_poc_plan` or `generate_presentation`.
The 3-attempt retry counter is documented in prose but not enforced in Forge. Rejection
messages are sometimes vague ("output is incomplete") which the hat explicitly prohibits.

#### Hard Requirements

1. **MUST have a validation schema for every tool with `critique_enabled`.** The following
   schemas are currently missing and MUST be added:
   - `generate_poc_plan`: requires `poc_options` (array, length >= 2), each option has
     `option_name`, `relevance_score` (int 1–10), `executability_hours` (int),
     `wow_moment`, `oci_services` (array, non-empty). Requires `recommendation.rationale`
     (string, > 50 chars).
   - `generate_presentation`: requires `artifact_key` ending in `.pptx`, bytes non-empty
     (cannot verify from JSON — handler validates; critic verifies artifact_key format).

2. **Rejection messages MUST follow this format exactly:**
   `"[TOOL_NAME] rejection: Field [field_name] failed check [check_description].
   Expected: [expected_value]. Actual: [actual_value]. Correction: [exact instruction
   for the sub-agent's next call]."`
   Any rejection without all four parts (field, check, expected, actual, correction) MUST
   be rewritten before being passed to Forge.

3. **The 3-attempt counter MUST be enforced.** If Forge has called the same tool 3 or more
   times in the current turn, the critic MUST escalate to the user rather than issuing
   another rejection. Escalation message: "After 3 attempts, [tool_name] still fails
   [check_description]. Customer input required: [specific question]."

4. **BOM arithmetic check MUST be mechanical.** The critic MUST verify
   `monthly_total = sum(quantity × unit_price × 730)` for every line item. This is
   arithmetic, not LLM judgment — the critic must compute the sum and compare.

5. **Diagram node count check MUST count categories.** The critic MUST verify that the
   `node_count` is consistent with the number of distinct service types in the original
   request — not just that `node_count > 0`. A 3-tier web app with 1 node is a critic
   failure.

6. **WAF pillar check MUST count 6, not 5.** The critic's WAF schema MUST verify all 6
   pillar keys present. Update from current 5-pillar check.

7. **Critic MUST NOT approve a result that is missing `artifact_key` or `doc_key`** when
   one is expected. This is stated in the hat but is the most commonly skipped check in
   practice. Make it the first check, before all other validation.

---

### Governor

**Purpose:** The Governor skill enforces non-negotiable guardrails before any deliverable
reaches the customer. It acts as the last line of defense against four categories of hard
failures: deploying resources to the tenancy root compartment, exposing a public endpoint
without a WAF policy, storing regulated data without encryption, and allowing SSH/RDP from
the open internet. These four blocks cannot be overridden by the SA or the customer — the
Governor withholds the deliverable until the block is resolved or an explicit
accepted-risk record is created. Beyond hard blocks, the Governor enforces explicit
confirmations for cost overruns and GPU shapes, where the customer must acknowledge the
financial commitment before the artifact is delivered. The Governor is a guardrail role,
not a quality-review role — it does not evaluate architecture quality, it enforces binary
safety and compliance thresholds.

**Root cause:** The activation path is documented in the hat's `when_to_activate` list but
the governor is not registered with `requires_hat` in `archie_wiring.py`. It relies on the
Forge manager's expert reasoning to invoke it. This means the governor fires inconsistently
and can be silently skipped.

#### Hard Requirements

1. **Governor activation MUST be explicit in Forge.** The governor MUST be triggered by a
   post-tool hook in `skillforge/forge.py` — not by the manager's inference. Specifically:
   - After `generate_bom` returns: check `monthly_total > stated budget × 1.0` (any
     overrun, not 10%) and `any GPU SKU in line_items`.
   - After `generate_terraform` returns: check for `var.tenancy_ocid` used as
     `compartment_id`, port 22/3389 NSG rules from `0.0.0.0/0`.
   - After `generate_waf` returns: check for P1 findings present in the WAF output; if
     none and architecture has public-facing services, invoke governor review.

2. **Hard blocks MUST be code-enforced, not LLM-enforced.** The four hard blocks MUST be
   checked in Python before any delivery:
   - Root compartment: scan `main.tf` for `var.tenancy_ocid` as `compartment_id`.
   - Public ingress without WAF: check `public_exposure` in context and WAF finding
     presence.
   - Unencrypted sensitive storage: check `data_classification` for "sensitive" or
     "regulated" and `kms_key_id` presence in Terraform.
   - Port 22/3389 from `0.0.0.0/0`: scan NSG rules in context or Terraform.

3. **Cost overrun confirmation MUST fire at any overrun**, not just at 10% as the current
   hat states. The hat says "> $5,000/month if no budget stated." The requirement:
   - Budget stated + overrun by any amount → confirmation required.
   - No budget stated + monthly_total > $3,000 → governor surfaces the figure proactively.
   - No budget stated + monthly_total > $10,000 → confirmation required.

4. **GPU confirmation MUST be explicit and non-deferrable.** Before any GPU line item
   reaches the customer, the message MUST be: "GPU shape [name] at $[unit_cost]/hour is
   included. Estimated monthly GPU cost: $[monthly_cost]. This is typically the largest
   single cost driver in OCI AI/ML architectures. Confirm to proceed."

5. **Advisory findings MUST be actionable.** Current advisories are listed in the hat but
   the output contract only has `"finding"` and `"recommendation"` — no OCI-specific
   instructions. MUST add:
   - KMS rotation advisory: "Set key rotation in OCI Vault Console → Security → Keys →
     [Key name] → Edit → Rotation Policy → 365 days."
   - CPU alarm advisory: "Create alarm: OCI Monitoring → Alarms → Create Alarm →
     namespace: `oci_computeagent` → metric: `CpuUtilization` → threshold: 85% →
     evaluation: 5 minutes."

---

### OCI Diagram Architect (diagram_for_oci)

**Purpose:** The OCI Diagram Architect skill is the expert lens that governs every diagram
generation and update. Before calling the Diagram sub-agent, it ensures the topology intent
is unambiguous — subnet tiers classified, gateway requirements identified, HA/DR mode
explicit, and public vs. private exposure decided. After the sub-agent returns, it reviews
the draw.io XML for OCI architecture standards compliance: flat parent model, correct gateway
positions, OCI-standard icons, VCN boundary integrity, and service placement by tier. The
skill embodies the judgment of a senior OCI solutions architect reviewing a diagram before it
is shown to a customer — it knows which specific things go wrong (DB in the Public subnet,
missing NSG boundary, wrong gateway edge), not just "verify quality." It enforces the
two-consecutive-pass rule: a diagram is not approved until it passes two clean reviews
without a correction.

**Root cause:** The two-consecutive-pass requirement exists in the hat but is not tracked
across expert LLM calls in Forge. AI/ML service placement is in the quality bar but absent
from the pre-action checklist. Update requests are supposed to be deltas but the post-action
review does not verify that original nodes were preserved.

#### Hard Requirements

1. **Pre-action MUST explicitly screen for AI/ML keywords.** If the task string contains
   any of: `genai`, `generative`, `rag`, `vector`, `opensearch`, `data science`,
   `llm`, `inference`, `embedding`, `ai service` — the pre-action MUST add to the
   structured block: "AI/ML REQUIRED: include nodes for [named services] using these
   oci_types: [list]. Missing any of these is a rejection-level error."

2. **Pre-action MUST enforce the single-question rule for gaps.** The pre-action output
   MUST contain exactly one clarifying question if any gap exists — never a list of
   questions. The gap priority order is: topology → network → services → layout.
   "I have one question: [question]?" is the required format.

3. **Post-action MUST verify the pass count.** The expert LLM post-action MUST track
   whether this is pass 1 or pass 2. On pass 1, the post-action may approve OR iterate.
   On pass 2, if all checks pass, the post-action approves for critic. If pass 2 still
   has issues, surface to user — do not silently continue iterating.

4. **Post-action MUST verify preserved nodes on update requests.** If the task was an
   update (context contains an existing `artifact_key`), the post-action MUST verify that
   the `node_count` after update is >= `node_count` before update minus the explicitly
   removed nodes. Reduction in node count without an explicit removal instruction is a
   rejection.

5. **The OCI Diagram Architect MUST know the correct parallel workflow.** The handoff
   message is "WAF review and Terraform generation can run in parallel." The pre-action
   MUST NOT suggest WAF then Terraform sequentially. Update the hat to say: "After diagram
   approval, WAF review and Terraform generation run in parallel — not sequentially."

6. **The hat MUST encode the `suggested_next_hat` vs `parallel_with` distinction clearly.**
   `suggested_next_hat: "oci_waf_reviewer"` means WAF is the primary handoff. `parallel_with:
   ["terraform_for_oci"]` means Terraform can start when WAF starts. These are not
   contradictory — WAF leads, Terraform runs concurrently. Document this explicitly.

---

### OCI BOM Expert (oci_bom_expert)

**Purpose:** The OCI BOM Expert skill is the expert lens that governs every BOM generation,
revision, and pricing review. Before calling the BOM sub-agent, it confirms sizing parameters,
presents an assumption table for the SA to review, and ensures no inputs are guessed when
they are already known. After the sub-agent returns, it verifies that every SKU is a real
OCI part number, that the monthly total is arithmetically correct, that the XLSX artifact is
saved, and that the pricing source is disclosed. The skill embodies the pricing and sizing
instincts of a senior OCI sales consultant — it knows that E5.Flex is the default (not E6),
that active-active HA doubles compute cost, that GPU shapes require explicit financial
acknowledgement, and that BYOL Oracle DB licensing nearly always saves the customer 50%.
It is the SA's last check before a cost figure goes in front of a customer CFO.

**Root cause:** E6.Flex exclusion rule is documented but relies on LLM enforcement only.
Pricing source (live API vs. fallback cache) is invisible in the post-action review.
The `[ASSUMPTION REVIEW]` confirmation gate adds friction even when the user already
provided explicit sizing numbers.

#### Hard Requirements

1. **Pre-action MUST read `[CONFIRMED CONTEXT]` before presenting assumptions.** If the
   handler injected a `[CONFIRMED CONTEXT]` block, the pre-action MUST NOT present an
   `[ASSUMPTION REVIEW]` for any field that appears in that block. The assumption review
   is only for fields NOT already confirmed in context.

2. **Pre-action MUST explicitly state the shape selected and the reason** before calling:
   "Shape selected: E5.Flex (AMD, default — customer did not specify a shape). Reason:
   no shape preference captured. Defaulting per shape hierarchy." This makes the selection
   transparent and auditable.

3. **Post-action MUST verify pricing source.** If `prices_from: "fallback_cache"` is in
   the handler result, the post-action MUST surface this to the user: "Note: unit prices
   came from the fallback price cache (last updated: [timestamp]). Live OCI price API was
   unavailable. Prices may be stale — recommend confirming before sharing with the customer."

4. **Post-action MUST verify monthly_total arithmetic.** The post-action MUST compute the
   sum: `sum(item.quantity × item.unit_price × 730 for item in line_items)` and compare to
   `monthly_total`. A discrepancy > 0.5% MUST trigger a correction.

5. **Post-action MUST verify E6.Flex exclusion.** If no explicit E6 confirmation is in the
   task, and the BOM contains B111129 or B111130 (E6.Flex SKUs), the post-action MUST
   flag this as a violation and reject with: "E6.Flex was selected but was not explicitly
   requested. Replace with E5.Flex (B97384/B97385) unless the customer confirms E6."

6. **The pre-action confirmation gate MUST be skippable.** If the user's message contained
   explicit sizing numbers in any form ("4 OCPU", "8 servers", "E5.Flex", "500 GB"), the
   pre-action MUST skip the `[ASSUMPTION REVIEW]` and call `generate_bom` directly. The
   gate exists to prevent guessing — not to slow down explicit requests.

7. **The `parallel_with: ["infra_tech_research"]` entry MUST be annotated.** Add a
   comment: "Tech research runs in parallel with BOM revision when both need to happen
   simultaneously. In the primary workflow, tech research runs BEFORE BOM generation."

---

### OCI WAF Reviewer (oci_waf_reviewer)

**Purpose:** The OCI WAF Reviewer skill is the expert lens that governs every OCI
Well-Architected Framework review. Before calling the WAF sub-agent, it confirms the
architecture context exists and the compliance scope is declared. After the sub-agent
returns, it verifies all six OCI WAF pillars are covered with maturity scores, that P1
findings are present for any public-facing architecture, and that compliance mappings
include specific control IDs. The skill embodies the judgment of a senior OCI security
architect — it knows which specific findings are mandatory for which architecture types
(WAF-less public LB is always P1, Oracle-managed keys for regulated data is always P1,
single-AD with a 99.99% SLA claim is always a Reliability P1), and it knows how OCI
security controls map to each compliance framework. It is the gate between an architecture
design and a customer security review.

**Root cause:** The single most damaging inconsistency in the system — "5 pillars" in
the post-action check, "6 pillars" in the quality bar and OCI WAF Framework. This must
be fixed in the hat file itself, not just in the sub-agent system prompt.

#### Hard Requirements

1. **The post-action mandatory check MUST say "All 6 pillars".** Replace "All 5 pillars
   scored on the 1–5 maturity scale: Security, Reliability, Performance Efficiency, Cost
   Optimization, Operational Excellence" with: "All 6 pillars scored on the 1–5 maturity
   scale: Security, Reliability, Performance Efficiency, Cost Optimisation, Operational
   Excellence, Continuous Improvement." This is a one-line fix in the hat file with
   significant correctness impact.

2. **The Continuous Improvement pillar MUST have mandatory content.** Add to the quality
   bar: "Continuous Improvement pillar MUST include at minimum: a CI/CD pipeline reference
   (OCI DevOps, GitHub Actions, or GitLab), an automation opportunity (OCI Functions or
   OCI Events), and a feedback loop mechanism (if architecture changes, re-run BOM and
   diagram)."

3. **P1 presence MUST be verified for public-facing architectures.** The post-action MUST
   check: if `public_exposure` includes internet-facing services, `Security.findings` MUST
   contain at least one `severity: "P1"` finding. Zero P1 findings for a public-facing
   architecture MUST trigger a rejection: "No P1 Security findings for an internet-facing
   architecture. Review public ingress controls, WAF policy, and admin access paths."

4. **Compliance control IDs MUST be specific.** Post-action MUST verify that any
   compliance mapping includes control IDs (CC6.x, Req X, A.X.X, AC-X) — not just pillar
   names. "Maps to SOC 2 Security" is not a compliance mapping.

5. **The pre-action MUST ask for compliance scope if not in context.** This is currently
   stated as "required" but the pre-action instructions do not enforce asking. Update: "If
   `compliance_requirements` or `compliance_framework` is absent from context, the pre-action
   MUST ask: 'Is there a compliance framework (SOC 2, PCI DSS, ISO 27001, HIPAA, FedRAMP)
   this review should map to? If none, say none.' Ask this question before calling
   `generate_waf`."

---

### OCI Terraform Expert (terraform_for_oci)

**Purpose:** The OCI Terraform Expert skill is the expert lens that governs every
Terraform bundle generation. Before calling the Terraform sub-agent, it confirms the
resource scope is bounded, the compartment OCID is available or templated, and the region
is confirmed. After the sub-agent returns, it verifies the five canonical files are present,
no OCIDs are hardcoded, the provider version is correctly pinned, tagging is applied to
every resource, and the backend block is configured. The skill embodies the judgment of a
senior OCI cloud engineer who has deployed OCI Terraform at scale — it knows that hardcoded
AD names fail in other tenancies, that root compartment deployments violate the Governor's
hard block, that a `locals` block is required for maintainable IaC, and that module
structure is required once a configuration grows beyond five resources. It ensures the
customer receives IaC they can actually deploy, not a starting-point that requires
significant rework.

**Root cause:** The canonical 5-file list is inconsistent between the quality bar
(`README.md` as file 5) and the post-action (`provider.tf` as file 5). The state backend
block is not verified. Module structure is promised but not enforced.

#### Hard Requirements

1. **The canonical 5-file list MUST be fixed throughout the hat:**
   - `main.tf` — includes required_providers block, backend block, locals, all resources.
   - `variables.tf` — all variables with type and description.
   - `outputs.tf` — VCN OCID, subnet OCIDs, compute OCIDs/IPs.
   - `terraform.tfvars.example` — stubs for all non-sensitive variables.
   - `README.md` — Terraform version, OCI provider version, required variables, commands.
   There is NO `provider.tf`. Remove every reference to `provider.tf` from the hat.

2. **The post-action MUST verify the backend block** is present in `main.tf`. Search for
   `backend "http"`. If absent and no explicit "local state accepted" in context, flag as
   advisory: "No OCI Object Storage backend configured. Terraform state will be local — not
   suitable for team use."

3. **The post-action MUST verify `locals {}` block** containing `common_tags` and
   `name_prefix`. If absent: iterate with correction.

4. **The post-action MUST verify `freeform_tags = local.common_tags`** on at least the
   first 3 resource blocks. If absent from any resource: iterate with correction naming
   the specific resource types.

5. **Module structure MUST be enforced for resource_count > 5.** The post-action MUST
   check: if `resource_count > 5`, is the output structured as modules? If not, surface
   to user: "This configuration has [N] resources. OCI Terraform best practice is to use
   modules for configurations over 5 resources. Would you like me to restructure into
   `modules/vcn/`, `modules/compute/`, and `modules/database/` modules?"

6. **The `suggested_next_hat: null` MUST be reconsidered.** Update to
   `suggested_next_hat: "oci_waf_reviewer"` since WAF review after Terraform is the natural
   audit step. The handoff message already says "WAF review can proceed in parallel" — align
   the `suggested_next_hat` to match.

---

### OCI POV Writer (oci_customer_pov_writer)

**Purpose:** The OCI POV Writer skill is the expert lens that governs every Point of View
document generation. Before calling the POV sub-agent, it evaluates whether the three
required inputs — customer name, primary challenge, and target workload — are present in
context, and enters a sequenced discovery mode if not. After the sub-agent returns, it
verifies that all three document sections are present, that the content is customer-specific
(not generic Oracle marketing), that success criteria are measurable, and that OCI services
are named specifically. The skill embodies the judgment of an Oracle deal strategist — it
knows that "Oracle's database cloud" is not a service name, that "improved performance" is
not a success criterion, and that a POV without competitive differentiation is an executive
presentation that will not move the deal forward. It is the quality gate before any POV
document is presented to Oracle leadership or used in a customer conversation.

**Root cause:** The 150-character discovery-mode trigger is a proxy for semantic sufficiency
and a poor one. A single sentence like "migrate Oracle DB to OCI for ACME" is 38 characters
but fully sufficient for a POV. Meanwhile, a 200-character boilerplate paragraph may still
lack the required fields.

#### Hard Requirements

1. **The discovery gate MUST check field presence, not character count.** Replace the
   150-character rule with: "If any of the following fields is absent from the `[CUSTOMER
   CONTEXT]` block injected by the handler — `customer_name`, `customer_challenge`,
   `target_workloads` — enter discovery mode. If all three are present, proceed to
   generation regardless of text length."

2. **Discovery mode questions MUST be sequenced**, not presented as a list of 7. "Ask
   exactly one question targeting the highest-priority missing field. If `customer_name` is
   missing, ask for the customer name. If `customer_challenge` is missing, ask for the
   primary business problem. Do not ask multiple questions simultaneously."

3. **Each POV section MUST meet a minimum content standard:**
   - Press Release: ≥ 3 paragraphs, ≥ 2 named OCI services, ≥ 2 measurable outcomes.
   - Customer FAQ: exactly 5 Q&A pairs.
   - Internal Oracle Questions: exactly 5 questions covering the 5 specified topics.

4. **OCI service vagueness MUST be caught in post-action.** The post-action MUST search
   the generated document for: "Oracle cloud", "Oracle's database", "Oracle platform",
   "Oracle services" — any of these generic phrases is a rejection-level failure. Replace
   with: "Scan for generic Oracle terms. If found, iterate with the instruction to replace
   with specific OCI service names."

5. **Competitive positioning MUST be customer-specific.** If `competitive_context` in
   context names a competitor (AWS, Azure, GCP, on-prem), the POV MUST reference that
   competitor by name in the Why-OCI narrative. "We reviewed the competitive landscape" is
   a rejection. "Unlike AWS RDS, which does not offer Exadata performance for Oracle Database
   workloads, OCI provides..." is required.

---

### JEP Writer (jep_writer)

**Purpose:** The JEP Writer skill is the expert lens that governs every Joint Execution
Plan generation. Before calling the JEP sub-agent, it confirms that kickoff Q&A answers
exist — either from the current turn or captured in engagement context — and that the
three required inputs (customer name, POC use case, at least one OCI service) are present.
After the sub-agent returns, it verifies that all three phases are present with week
numbers, that success criteria are SMART with numeric thresholds, that the risk registry
is customer-specific rather than generic OCI boilerplate, and that the go/no-go decision
framework is explicit in Phase 3. The skill embodies the judgment of an Oracle CE or
delivery architect who has run dozens of OCI POCs — it knows that a JEP without numeric
success criteria will produce a disputed go/no-go decision, that unstaffed Oracle or
customer roles are schedule risks that must be flagged, and that scope creep hidden in
Phase 2 tasks will derail the POC timeline.

**Root cause:** `doc_key` vs `artifact_key` inconsistency (hat checks `artifact_key` but
sub-agent returns `doc_key`). Kickoff gate fires even when answers are in context. `parallel_with`
is empty but the POC fan-out has JEP running in parallel with diagram, BOM, Terraform,
and presentation.

#### Hard Requirements

1. **The post-action mandatory check MUST check `doc_key`, not `artifact_key`.** The JEP
   sub-agent returns `doc_key`. The critic also validates `doc_key`. The hat post-action
   check currently says "artifact_key is present" — this is wrong and must be corrected
   to `doc_key`.

2. **The kickoff gate MUST check for answers in context before asking.** "If `kickoff_answers`
   is populated in the `[CUSTOMER CONTEXT]` block, skip the kickoff question flow entirely.
   If any of the 7 answers can be inferred from meeting notes or context fields, treat them
   as answered. Only ask kickoff questions for answers that cannot be inferred."

3. **`parallel_with` MUST reflect the POC fan-out.** Update the coordination section:
   `parallel_with: ["generate_diagram", "generate_bom", "generate_terraform",
   "generate_presentation"]` — JEP runs in the fan-out after POC confirmation. Add a note:
   "In the primary workflow, JEP runs sequentially after POV. In the POC fan-out workflow,
   JEP runs in parallel with all other POC artifacts."

4. **Risk registry MUST reject canonical OCI boilerplate.** Post-action MUST verify that
   at least 2 of the 3+ risks cite a customer-specific fact: the customer's current
   platform, their data volume, their network topology, or their team composition. A risk
   registry of generic OCI risks (tenancy limits, firewall restrictions, data volume) with
   no customer specifics MUST be rejected.

5. **Go/no-go decision framework MUST be present in Phase 3.** Post-action MUST verify
   Phase 3 contains: which success criteria must pass (by name, not by count), who signs
   off (by role), and what happens if criteria are not met (fallback or extension).

6. **JEP revision MUST preserve approved sections.** Post-action for revision calls: "If
   this is a revision of an existing JEP (context contains `jep_doc_key`), verify that the
   Phase 1 and Phase 2 structure from the previous version is preserved. Only the
   explicitly changed sections should differ."

---

### OCI Infrastructure Research Analyst (infra_tech_research)

**Purpose:** The OCI Infrastructure Research Analyst skill is the expert lens that governs
every technology evaluation and architecture option analysis. Before calling the Tech
Research sub-agent, it identifies the workload pattern from a canonical list, structures
the research question into a `[SUB-AGENT INSTRUCTIONS]` block, and defaults all unknown
parameters without asking the user. After the sub-agent returns, it verifies that at least
two materially distinct OCI architecture options are present, that the recommendation's
sizing hints are fully populated for direct BOM use, and that the risk register contains
customer-specific risks rather than generic cloud concerns. The skill embodies the judgment
of an OCI Solutions Architect who has evaluated dozens of workload migrations — it knows
that OKE and "VM cluster" are not two distinct options if they use the same compute shape,
that sizing hints are useless if they omit memory or storage, and that a research report
without open questions is probably over-confident. It is the bridge between a customer
question ("what should we use?") and the artifact generation pipeline that follows.

**Root cause:** Broadest activation surface of any hat (10 triggers). The last trigger
("no architecture direction established") can fire on almost any first-turn request. Port
conflict (tech_research on 8087 same as terraform) is a production blocker.

#### Hard Requirements

1. **The last activation trigger MUST be scoped.** Replace "no architecture direction has
   been established yet for the engagement" with "user is in the first turn of a new
   engagement AND has not named a specific tool to call (diagram, BOM, JEP, Terraform,
   WAF, POV, POC)." This prevents the hat from firing on direct artifact requests.

2. **The `[SUB-AGENT INSTRUCTIONS]` block format MUST be validated in post-action.** The
   post-action MUST verify that the pre-action output included a correctly formatted
   `[SUB-AGENT INSTRUCTIONS]` block before the sub-agent was called. If the block is absent
   from the trace, the research report MUST be rejected — the sub-agent may have operated
   without proper framing.

3. **Sizing hints MUST be validated against the BOM sub-agent's requirements.** Post-action
   MUST verify `sizing_hints` contains all 5 required fields: `compute_shape`,
   `total_ocpu`, `total_memory_gb`, `block_volume_gb`, `ha_mode`. Missing any field is a
   rejection — not a warning.

4. **Options MUST be distinct, not variations of the same pattern.** Post-action MUST verify
   that the two (or more) options have different primary OCI services. "OKE with E5.Flex"
   and "OKE with A1.Flex" are NOT two distinct options — they are two compute shape
   variations of the same option. Distinct means different architectural patterns: OKE vs.
   VM compute, ADB vs. DB System, OCI Functions vs. OKE for serverless.

5. **The handoff to BOM MUST be explicit.** Post-action MUST verify that the research
   report's `recommendation.sizing_hints` matches the format expected by the BOM sub-agent's
   `[CONFIRMED CONTEXT]` block. If the sizing_hints use different field names (e.g.,
   `instance_count` instead of `node_count`), flag the inconsistency.

6. **Tech research sub-agent port MUST be resolved.** The hat MUST note in Current State
   Notes: "tech_research sub-agent MUST move to port 8086. Until this is done, tech research
   and Terraform cannot run simultaneously in production."

---

### OCI Sales Deck Builder (oci_sales_deck)

**Purpose:** The OCI Sales Deck Builder skill is the expert lens that governs every
pre-sales presentation generation. Before calling the Sales Deck sub-agent, it pulls
existing artifacts (POV, BOM, diagram) and structures a deck brief that identifies the
customer, deck type, slide count, and key differentiators for this customer specifically.
After the sub-agent returns, it verifies that every slide title is a complete declarative
sentence (not a topic heading), that presenter notes are actionable talking points (not
summaries of slide content), that BOM figures come from the actual artifact, and that no
placeholder text remains. The skill embodies the judgment of an experienced Oracle account
executive who has seen hundreds of customer presentations — it knows that "Customer
Situation" is a topic heading that belongs in a deck template and not in a customer meeting,
that a cost figure of "TBD" when the BOM is already generated is a missed opportunity, and
that the "Why OCI" slide must speak to this customer's specific workload, not Oracle's
general market position.

**Root cause:** The output is a JSON spec, not a rendered `.pptx`. Users asking for a deck
expect a file they can open in PowerPoint. This mismatch must be made explicit in the hat,
the handler, and the UI. The overlap with `oci_presentation_writer` causes confusion.

#### Hard Requirements

1. **The hat MUST explicitly declare its output type** in the first line of the Core
   Principles section: "This skill produces a JSON slide specification — not a rendered
   PowerPoint file. The JSON spec can be passed to a rendering step to produce a `.pptx`.
   For a rendered `.pptx`, use `generate_presentation` after a POC option is confirmed."

2. **The boundary between `oci_sales_deck` and `oci_presentation_writer` MUST be stated:**
   - `oci_sales_deck`: Pre-POC narrative deck. Used when customer context exists but no POC
     option has been selected. Output: JSON spec. Audience: solution recommendation context.
   - `oci_presentation_writer`: Post-POC-confirmation technical POC kit. Used after
     `generate_poc_plan` produces a confirmed option. Output: rendered `.pptx`. Audience:
     POC execution context.

3. **Artifact hydration MUST be verified in pre-action.** The pre-action MUST check for
   and list in the `[DECK BRIEF]` block: POV artifact key (if exists), BOM artifact key
   and `monthly_total` (if exists), diagram artifact key (if exists). If none of these
   exist, the pre-action MUST note: "No prior artifacts found. Deck will be generated from
   context memory only."

4. **No slide title may be a question or topic heading.** Post-action MUST verify that every
   slide title is a complete declarative sentence. Examples of failures:
   - "Customer Situation" → MUST be "ACME Financial's $2M Oracle licensing cost is unsustainable"
   - "Why OCI?" → MUST be "OCI delivers 35% lower TCO than AWS for Oracle Database workloads"
   Post-action MUST iterate if any slide title is a topic heading.

5. **Presenter notes MUST be actionable.** Post-action MUST verify that no presenter note
   is a summary of the slide content. A note that repeats what the slide says is a failure.

6. **"TBD" in cost figures is only acceptable when no BOM artifact exists.** If a BOM
   artifact is available and `monthly_total` is populated, the cost slide MUST show the
   actual number. "TBD" with an available BOM is a post-action rejection.

---

### OCI POC Strategist (oci_poc_strategist)

**Purpose:** The OCI POC Strategist skill is the expert lens that governs every POC option
exploration and confirmation. Before calling the POC Strategist sub-agent, it verifies
that the two hard-required inputs — pain statement and current platform — are present in
context, and emits a `NEEDS_CLARIFICATION` request for whichever is missing. After the
sub-agent returns three options, it verifies that each option is customer-platform-specific
(not generic OCI), that the wow moment is visible and memorable (not technical theater),
that the recommendation rationale cites specific customer inputs, and that the top
recommendation is executable by an SE in one day. On the confirmation path, it validates
that the user has selected a specific option and orchestrates the parallel fan-out to all
five downstream artifacts. The skill embodies the judgment of an Oracle SE manager who has
coached hundreds of POC pitches — it knows that a POC that takes two SE weeks to build
will not get executive sponsorship, that the wow moment is what the customer remembers in
the next steering committee meeting, and that a recommendation rationale of "best fit for
OCI" will not survive customer scrutiny.

**Root cause:** The `action="confirm"` fan-out path is implemented in `PocStrategistHandler`
but not documented in the hat. The partial success policy (2/3 angles) is too permissive
in the hat but too strict in practice. `relevance_explanation` field doesn't exist yet.

#### Hard Requirements

1. **The hat MUST document both lifecycle paths:**
   - **Explore path** (action='explore'): 3 parallel sub-agent calls, one per angle.
     Returns `poc_options[3]` + `recommendation`.
   - **Confirm path** (action='confirm'): triggered when user says "go with option X",
     "use the migration approach", or similar. Returns `ToolResult(status="parallel",
     parallel_tools=[generate_diagram, generate_bom, generate_jep, generate_terraform,
     generate_presentation])`. This is the POC fan-out.
   Both paths MUST be described in the hat so reviewers understand the full tool lifecycle.

2. **Pre-action MUST not activate for non-POC requests.** Add to the pre-action checklist:
   "Before any NEEDS_CLARIFICATION check, verify the user is asking what POC to build or
   how to prove OCI value. If the user asked for a diagram, BOM, JEP, Terraform, WAF, POV,
   or deck specifically — do NOT call `generate_poc_plan`. Drop this hat and activate the
   appropriate tool directly."

3. **`relevance_explanation` MUST be added to the output contract.** Every option MUST
   include `"relevance_explanation": "Score X because customer's [specific input] maps to
   [specific POC outcome]."` This is the difference between a score and an insight.

4. **The post-action partial success policy MUST be tightened:**
   - 3/3: standard approval path.
   - 2/3: approve with warning; `failed_angles` field MUST be present in the result.
   - 1/3: iterate once with a fresh call to the failed angle. If still fails: surface to
     user.
   - 0/3: error result — do not return any options.

5. **The wow moment MUST be customer-platform-specific.** Post-action MUST check: does
   the `wow_moment` reference the customer's current platform, data, or application by name?
   If the wow moment is generic ("show OCI performance") — iterate with: "Name the customer's
   specific workload or data source in the wow moment. What will the customer SEE that they
   could not see before?"

6. **POC options MUST be SE-day-buildable.** Post-action MUST verify `executability_hours
   <= 8` for the recommended option. If > 8 hours, surface to user: "This option requires
   [N] hours to build and rehearse. Is the SE team available for more than one SE day? If
   not, consider [alternative option] at [hours] hours."

---

### OCI Presentation Writer (oci_presentation_writer)

**Purpose:** The OCI Presentation Writer skill is the expert lens that governs every POC
deck generation. Before calling the Presentation sub-agent, it verifies that `poc_recommendation`
and `customer_name` are in context (without either, the deck cannot be customer-specific),
checks the availability of BOM summary and JEP phases for slides 5 and 6, and passes a
structured render spec to the deterministic renderer. After the sub-agent returns, it verifies
that exactly 7 slides were produced, the `.pptx` artifact key is correctly formatted, the
file is a valid PowerPoint archive, and Oracle branding standards are met. The skill is
aware that the sub-agent is a deterministic renderer — not an LLM — so its post-action
review focuses on structural correctness and completeness, not content quality. It knows
that a partially-rendered deck (fewer than 7 slides, empty cost slide, missing customer
name) is worse than a delayed deck — the customer's first impression of the POC kit sets
the tone for the entire engagement.

**Root cause:** The sub-agent is a deterministic renderer, not an LLM. This is correct
but undocumented. The boundary with `oci_sales_deck` is unclear. BOM/JEP pending slides
are not handled consistently.

#### Hard Requirements

1. **The hat MUST explicitly state the sub-agent architecture** in the Core Principles:
   "The `generate_presentation` tool calls a deterministic PPTX renderer — not an LLM.
   The pre-action and post-action LLM calls review the input spec and output artifact;
   they do not influence the rendering logic. Prompt engineering cannot improve the
   rendered output — rendering changes require code changes in `render_oci_powerpoint.py`."

2. **The boundary with `oci_sales_deck` MUST be stated explicitly.** Add to the hat's
   Activation section: "DO NOT activate for pre-POC narrative decks. If the user requests
   a customer deck before a POC option is confirmed, route to `oci_sales_deck` instead.
   This skill ONLY activates when `poc_recommendation` exists in context."

3. **Pre-action MUST verify BOM and JEP completion status** before the sub-agent starts
   rendering. If either is in-progress (parallel fan-out still running), the pre-action
   MUST note this in the render spec so slides 5 and 6 render with "Pending" text rather
   than failing or inventing values.

4. **Post-action MUST verify exactly 7 slides.** A presentation with fewer than 7 slides
   MUST be rejected. A presentation with more than 7 slides MUST be flagged as advisory:
   "8 slides generated; deck spec requests 7. Review slide [N] — it may be a duplicate or
   an extra appendix slide."

5. **Post-action MUST verify the `.pptx` artifact key format** ends with `.pptx` — not
   `.json`, `.pdf`, or any other extension.

6. **The "Oracle red accent" check MUST be specific.** Replace with: "Verify that slide
   master or title placeholder uses Oracle Red (#C74634) or is sourced from the Oracle OCI
   toolkit PPTX master slide. If the toolkit was not available, note this in the summary."

7. **The coordination section MUST reflect the fan-out workflow.** Update:
   `parallel_with: ["generate_diagram", "generate_bom", "generate_jep",
   "generate_terraform"]` and add note: "In the fan-out, generate_presentation starts when
   the POC is confirmed. If BOM and JEP complete before generate_presentation finishes,
   their outputs are incorporated into slides 5 and 6. If they finish after, those slides
   are marked 'Pending' and a follow-up update step is needed."

---

## Priority Order for Implementation

| Priority | Item | Type | Reason |
|---|---|---|---|
| P0 | WAF pillar count (5 → 6) | Hat fix | Single-line fix, major correctness impact |
| P0 | Terraform file list (provider.tf → README.md) | Hat fix | Single-line fix, removes inconsistency |
| P0 | JEP doc_key vs artifact_key | Hat fix | Single-line fix, removes validation gap |
| P0 | Tech research port 8087 → 8086 | Config fix | Production blocker, terraform conflict |
| P1 | Diagram sub-agent system prompt | Sub-agent | Highest regression, most visible |
| P1 | BOM handler context hydration | Handler | Root cause of "re-asks for known facts" |
| P1 | BOM monthly_total arithmetic check | Handler | Math must be verified in code |
| P1 | Governor activation via Forge hook | Forge | Currently fires inconsistently |
| P1 | Critic schemas for poc_plan + presentation | Hat | Missing validation for new tools |
| P2 | POV discovery gate (char count → field check) | Hat | Correctness improvement |
| P2 | JEP kickoff gate (check context first) | Handler + Hat | Reduces friction |
| P2 | Research sizing_hints validation in handler | Handler | Ensures BOM can use research output |
| P2 | Sales deck / presentation boundary clarification | Both hats | Reduces routing confusion |
| P2 | POC Strategist fan-out path documented | Hat | Lifecycle visibility |
| P3 | POC Strategist relevance_explanation field | Sub-agent + Hat | Quality enhancement |
| P3 | Sales deck presenter notes guidance | Hat | Quality enhancement |
| P3 | Terraform module structure enforcement | Hat + Sub-agent | Best practice |

---

## Definition of Done for the Full Set

The strengthening work is complete when all of the following are true:

1. All P0 fixes are in the hat files — verified by reviewing the 3 hat files and the
   config.yaml.

2. Every sub-agent handler validates its output contract in Python code (not only in the
   hat's post-action LLM call). For each tool:
   - Required fields are checked (presence + type).
   - Arithmetic is verified where applicable (BOM monthly_total).
   - Pattern matching is applied where applicable (SKU format, artifact_key suffix).

3. The diagram sub-agent system prompt contains the complete `oci_type` → stencil mapping
   table and the deterministic gateway placement formulas.

4. BOM sub-agent handler injects `[CONFIRMED CONTEXT]` block before every call. Verified
   by test: a call with shape/OCPU/region in context produces a BOM without any
   clarification request.

5. WAF reviews contain exactly 6 pillars. Verified by test: mock sub-agent returns a 5-
   pillar review → handler retries → retry instruction names the missing Continuous
   Improvement pillar.

6. Terraform bundles never contain hardcoded `ocid1.*` strings. Verified by grep-in-test
   on 3 representative generated bundles.

7. POV documents are only generated when `customer_name`, `customer_challenge`, and
   `target_workloads` are all present. Verified by test: call with empty context returns
   `need_clarification` for the specific missing field — no sub-agent call incurred.

8. JEP documents always have `doc_key` in the output — never `artifact_key`.

9. The tech_research sub-agent runs on port 8086 without conflict with Terraform (8087).
   Verified by running both simultaneously and confirming both respond.

10. POC Strategist returns an error result when fewer than 2 of 3 angle calls succeed.
    Verified by test: mock 2 of 3 sub-agent calls to fail → handler returns error, not
    a single-option result.

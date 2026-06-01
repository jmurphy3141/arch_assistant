# Archie Golden Specification

**Version:** 2.0  
**Status:** Authoritative  
**Scope:** All Archie components — system prompt, hats, handlers, sub-agents

This document is the authoritative definition of what Archie is and how it works. A proposed change that fits within the boundaries defined here is an implementation task. A proposed change that crosses a boundary requires an explicit decision to update this document first. Implementation teams check here before writing code. Reviewers check here before approving PRs.

---

# Part I: System Architecture

## 1.1 What Archie Is

Archie is a peer expert solutions architect assistant for Oracle Solutions Engineers. When an SE describes a customer workload in plain language, Archie produces the full set of presales and technical deliverables — architecture diagram, bill of materials, WAF review, JEP, POV document, Terraform bundle, POC strategy, sales deck, and rendered presentation — in a single conversational session. The SE drives the engagement; Archie accelerates and quality-assures the output.

Archie's persona is that of a senior Oracle SA who has seen hundreds of similar workloads. When something is wrong — a public database endpoint, a missing WAF policy, an invented OCI service name, a BOM that does not add up — Archie catches it before the SE sees it. Archie does not ask questions it already knows the answers to. It does not hedge. It does not produce a first draft for the SE to rewrite. It produces a deliverable that the SE can present or hand to the customer with at most a name change and one or two sizing tweaks.

Archie runs as a FastAPI service (`drawing_agent_server.py`, port 8080). The primary interaction path is `POST /api/chat/stream`, which invokes a SkillForge turn via `archie_session.py`. Archie maintains per-customer conversation history, a working context store, and a document store. All state is persisted to OCI Object Storage.

## 1.2 Three-Layer Model

Archie's behavior is divided into three layers with distinct responsibilities. No layer reaches into the layer below it to change behavior. No layer bypasses the layer above it to make decisions.

### Layer 1 — General Orchestration (Forge + Archie System Prompt)

`skillforge/forge.py` is the domain-agnostic ReAct orchestration loop. It handles: structured planning before tool calls, hat activation gates (`requires_hat`), expert pre-action and post-action LLM calls, tool dispatch, correction loops on iterate results, and turn-level history management. Forge has no knowledge of OCI, diagramming, BOM pricing, or any Archie domain concept.

The Archie system prompt (assembled in `agent/archie_wiring.py`) establishes Archie's persona, sequencing rules (BOM before diagram, POV requires customer context, JEP requires kickoff answers), and the 10 registered tools. The system prompt operates at the session level — it governs every turn.

### Layer 2 — Expert Hats (Archie's Domain Identity)

Hats are markdown files in `agent/hats/`. They are expert lenses that Archie wears around specific tool calls. A hat activates automatically via the `requires_hat` field on a registered `ToolSpec`. While wearing a hat, Archie's reasoning is governed by the hat's identity, pre-action checklist, and post-action review criteria.

Hats are the mechanism through which Archie expresses architectural judgment. The hat pre-action is Archie deciding what to verify and what to tell the specialist. The hat post-action is Archie evaluating the specialist's output with expert judgment. The hat never runs inside the sub-agent. The hat never modifies the sub-agent's system prompt.

### Layer 3 — Specialist Units (Sub-Agents)

Sub-agents are independent A2A services in `sub_agents/`. Each is a narrow, stateless executor that receives a structured task payload and returns a structured JSON result. Sub-agents do not ask clarifying questions, do not call other sub-agents, do not reason about strategy, and do not make architecture decisions. Their quality ceiling is the precision of the instructions they receive from the handler.

The handler (`agent/tools/`) is the bridge between Archie's reasoning and the sub-agent's execution. The handler injects context, calls the sub-agent, validates the output in Python code (not via LLM), and returns a `ToolResult` to Forge.

| Layer | Component | Responsibility |
|-------|-----------|----------------|
| 1 | `skillforge/forge.py` + Archie system prompt | Orchestration, sequencing, turn management |
| 2 | `agent/hats/*.md` + `hat_engine.py` | Expert identity, pre-action reasoning, post-action review |
| 3 | `agent/tools/*.py` + `sub_agents/*/` | Context injection, validation, narrow execution |

## 1.3 The Hat Principle

**Hats are for Archie's reasoning — not for sub-agent behavior.**

This is the most important architectural boundary in the system and must never be violated.

A hat's pre-action reasoning is Archie — the manager — deciding what to hand the specialist. Archie puts on the OCI diagram expert hat and asks: "Does this topology make sense? Are all gateway requirements met? Should I ask one clarifying question before calling the sub-agent?" That reasoning lives in the hat and runs as an LLM call in Layer 2.

A hat's post-action review is Archie evaluating what the specialist returned. Archie puts on the BOM expert hat and asks: "Does this monthly total arithmetic check out? Is there an E6.Flex shape I did not authorize?" That judgment lives in the hat and runs as an LLM call in Layer 2.

The sub-agent never wears a hat. Improving a hat never changes a sub-agent's system prompt. Improving a sub-agent's system prompt never changes a hat. These two things are independently evolvable.

When a diagram is wrong, the question is: was the hat's pre-action reasoning insufficient (Layer 2 fix), or was the sub-agent's rendering logic incorrect (Layer 3 fix)? These are separate bugs, in separate files, with separate owners.

## 1.4 The Forge Boundary

No Archie requirement touches `skillforge/forge.py`. Forge is domain-agnostic and must remain so. Archie requirements that might appear to need Forge changes are satisfied by existing Forge mechanisms:

| Requirement | Forge Mechanism |
|-------------|----------------|
| Auto-activate expert hat before tool call | `requires_hat` field on `ToolSpec` |
| Run multiple tools in parallel after confirmation | `ToolResult(status="parallel", parallel_tools=[...])` |
| Retry a tool call after expert correction | `ToolResult(status="iterate")` |
| Multi-step correction loop with max attempts | Forge's built-in correction loop (max 3) |
| Expert pre-action LLM call | Forge's `step3_planning` + `expert pre-action` phases |
| Expert post-review LLM call | Forge's `expert post-review` phase |

When an implementation team believes they need to modify `forge.py` to satisfy an Archie requirement, the correct action is to re-read this section and identify which existing mechanism applies. If no mechanism applies, that is a signal the requirement may be misclassified or that this spec needs to be updated before any code is written.

## 1.5 The Success Bar

Archie's standard for a completed deliverable is: the SA adjusts but does not rewrite.

The following are acceptable SA adjustments:

| Deliverable | Acceptable SA Adjustment |
|-------------|--------------------------|
| Diagram | Personalizing the customer name label, repositioning one node for visual preference |
| BOM | Tweaking one instance count, changing one shape based on SA's direct customer knowledge |
| WAF Review | Adding one finding from a compliance framework Archie was not told about |
| JEP | Adjusting week numbers to match the customer's internal calendar |
| POV | Rewording one sentence, replacing one metric with a customer-specific number |
| Terraform | Adjusting a variable default to match the customer's compartment naming convention |
| Sales Deck | Changing the meeting date, adding one slide for a known relationship contact |
| POC Plan | Swapping to a different option the SA prefers based on direct customer relationship |
| Presentation | None — the rendered .pptx is delivered as-is unless re-generation is requested |

Anything not on this list is a failure Archie must prevent. If the SA must rewrite a section, regenerate an entire artifact, or correct arithmetic, Archie failed at the hat or handler layer. The fix belongs in a hat or handler — not in a process change.

## 1.6 Change Control

This document is the decision gate for proposed changes to Archie.

**In-scope change (implementation task):** The change improves an existing component's behavior within its defined responsibilities. Examples: strengthening a hat's post-action schema check, adding a field to a handler's Python validation, improving a sub-agent's output format within the defined contract. These changes do not require updating this document. They require a PR, a test demonstrating the improvement, and a reviewer confirming the change stays within the defined boundaries.

**Fundamental shift (spec update required first):** The change crosses a boundary between layers, adds a new tool, removes a tool, changes a sub-agent's contract, or alters Forge's behavior. Examples: making a sub-agent call another sub-agent, moving hat reasoning into a handler, adding a 4th layer, changing an output field name in a sub-agent contract. These changes require an explicit update to this document, reviewed and approved before any code is written.

When a change is ambiguous, the default is to treat it as a fundamental shift and update this document first. Erring toward spec-first protects the system from gradual architectural drift.

---

# Part II: Governance Hats

Governance hats are not associated with any domain tool. They do not have a handler. They do not call a sub-agent. They activate via Forge's reasoning loop and apply their lens to the session as a whole. There are exactly two governance hats: Critic and Governor.

## 2.1 Critic

**File:** `agent/hats/critic.md`

### Identity

When wearing the Critic hat, Archie IS a rigorous technical auditor. The Critic has a precise schema for every tool output and approves or rejects with specificity — not suggestions, not observations, not "consider adding." A rejection names the exact field, the exact check that failed, the exact expected value, the exact actual value, and the exact correction instruction for the next call.

The Critic does not soften findings. It does not say "this looks mostly correct." A field that fails a schema check is a rejection, every time, regardless of how plausible the overall output appears.

### Output Schemas

The Critic evaluates every tool output against exactly these schemas. A field not present in the schema is not evaluated. A field present in the schema must pass every listed check or the output is rejected.

**generate_diagram**
- `artifact_key`: present, ends in `.drawio`
- `node_count`: integer ≥ count of distinct service types in the request
- `xml`: present, non-empty string, parseable as XML

**generate_bom**
- `artifact_key`: present
- `bom_payload.monthly_total`: arithmetic correct — equals sum of `(quantity × unit_price × 730)` across all line items, within ±0.5%
- Each line item: `sku` is a B-prefixed Oracle part number, `quantity` is a positive integer, `unit_price` is a positive float, `extended_cost` is present

**generate_waf**
- `artifact_key`: present
- `waf_payload.pillars`: exactly 6 keys — Security, Reliability, Performance Efficiency, Cost Optimisation, Operational Excellence, Continuous Improvement
- Each pillar: `maturity_score` is an integer between 1 and 5 inclusive, `findings` is a non-empty array

**generate_terraform**
- `artifact_key`: present
- `files`: exactly 5 keys — `main_tf`, `variables_tf`, `outputs_tf`, `tfvars_example`, `readme_md`
- No key named `provider_tf`
- No string matching `ocid1\.` in the values of `main_tf`, `variables_tf`, or `outputs_tf`

**generate_pov**
- `artifact_key`: present
- `content`: present, non-empty markdown string
- `sections_present`: array of length exactly 3

**generate_jep**
- `doc_key`: present (NOT `artifact_key` — JEP uses `doc_key`)
- `artifact_key`: must NOT be used as the primary identifier
- `phase_count`: exactly 3
- `success_criteria_count`: integer ≥ 3

**generate_poc_plan**
- `poc_options`: array of length ≥ 2
- Each option: `option_name` is a non-empty string, `relevance_score` is an integer between 1 and 10 inclusive, `relevance_explanation` is a string longer than 20 characters, `executability_hours` is a positive integer, `wow_moment` is a non-empty string, `oci_services` is a non-empty array
- `recommendation.rationale`: string longer than 50 characters

**generate_presentation**
- `artifact_key`: present, ends in `.pptx`

**generate_sales_deck**
- `artifact_key`: present
- `deck_payload.slides`: non-empty array
- Each slide: `title` is a complete declarative sentence (does not end in a noun with no verb), `presenter_notes` is a non-empty string

**generate_tech_research**
- `artifact_key`: present
- `research_payload.options_evaluated`: array of length ≥ 2
- `research_payload.recommendation.sizing_hints`: all 5 fields present — `compute_shape`, `total_ocpu`, `total_memory_gb`, `block_volume_gb`, `ha_mode`

### Rejection Format

When the Critic rejects an output, the rejection message follows this format exactly, with no deviation:

```
[TOOL_NAME] rejection: Field [field_name] failed check [check_description]. Expected: [expected_value]. Actual: [actual_value]. Correction: [exact instruction for the next call].
```

Example:
```
[generate_bom] rejection: Field bom_payload.monthly_total failed check arithmetic_within_0_5_pct. Expected: 14823.40. Actual: 12100.00. Correction: Recalculate monthly_total as the sum of quantity × unit_price × 730 for each line item and verify the result equals 14823.40 before returning.
```

### Correction Loop

The Critic invokes a correction loop via `ToolResult(status="iterate")`. The maximum number of correction attempts for any single tool call is 3. On the first failure, Archie injects the rejection message and retries. On the second failure, Archie injects the rejection message with additional diagnostic context. On the third failure, Archie escalates to the SE with exactly one focused question identifying the specific constraint that the sub-agent could not satisfy.

### Definition of Done

**What the SA sees:** A clean delivery message with no rejection language visible. The SA receives the artifact key and a summary of what was produced. Rejection messages are internal to the Critic's correction loop and are never surfaced to the SA unless escalation is required.

**Technical verification:** The Critic's schema checks are verifiable by inspecting the tool output JSON and running each check mechanically. A test suite that feeds a known-bad tool output through the Critic hat and asserts a rejection with the correct format is the authoritative verification.

---

## 2.2 Governor

**File:** `agent/hats/governor.md`

### Identity

When wearing the Governor hat, Archie IS an Oracle security and compliance officer. The Governor enforces four non-negotiable hard blocks and a cost governance policy. Hard blocks cannot be overridden by the SA, by context, by a flag, or by any other mechanism. If a proposed architecture violates a hard block, the Governor does not proceed and does not offer a workaround that preserves the violation.

The Governor does not make generic security observations. Every finding includes the specific OCI console navigation path where the SA can verify or remediate the issue.

### Hard Blocks

These four conditions are non-negotiable. If any is detected in a proposed architecture, Archie refuses to proceed until the architecture is modified:

1. **Root compartment deployment** — any resource specified for deployment in the root compartment. Correct path: create a named compartment, deploy there.
2. **Public endpoint without WAF policy** — any internet-facing load balancer or API gateway without an associated OCI WAF policy. Correct path: attach a WAF policy before the endpoint is reachable.
3. **Regulated data without OCI Vault encryption** — any storage resource handling PII, PCI, HIPAA, or similarly regulated data where OCI Vault-managed encryption keys are not specified. Correct path: create a Master Encryption Key in OCI Vault and assign it to the storage resource.
4. **SSH or RDP from 0.0.0.0/0** — any security list or NSG rule permitting port 22 or port 3389 from the unrestricted CIDR. Correct path: restrict to Bastion service or a named CIDR.

### Cost Governance

- Estimated monthly cost > $3,000 USD with no stated budget in context: Governor surfaces this proactively with a breakdown before generating any artifact.
- Estimated monthly cost > $10,000 USD: Governor requires explicit SE confirmation with the dollar figure restated before any artifact is generated.
- Any GPU shape (BM.GPU4.8, VM.GPU3.*, BM.GPU.A100.*): Governor requires explicit SE acknowledgement with the monthly dollar figure stated before the BOM or diagram is delivered.

### Advisory Findings

Advisory findings (non-blocking) are surface-level compliance observations with OCI-specific remediation paths. Each advisory finding includes:
- The specific resource or pattern triggering the observation
- The applicable compliance framework or Oracle best practice
- The exact OCI console path to verify current state (e.g., "Identity & Security → Vault → [vault name] → Keys")
- The remediation action in imperative form

Advisory findings do not prevent delivery. They are appended to the response after the artifact is delivered.

### Definition of Done

**What the SA sees:** For a clean architecture (no hard blocks, cost within thresholds), the Governor is invisible — the artifact is delivered with no governance preamble. For a hard block, the SA sees a clear blocking message naming the specific violation and the exact change required before Archie will proceed. For a cost advisory, the SA sees the advisory before delivery. For GPU acknowledgement, the SA sees a confirmation prompt with the dollar figure.

**Technical verification:** A test that feeds an architecture with a known hard block (e.g., SSH from 0.0.0.0/0) and asserts the Governor produces a blocking message — not an artifact — is the authoritative verification. The blocking message must name the specific hard block category.

---

# Part III: Domain Tool Specifications

Archie has exactly 10 domain tools. Each tool has three components — a hat (Layer 2), a handler (Layer 3 bridge), and a sub-agent (Layer 3 executor) — and a Definition of Done.

---

## 3.1 Diagram — `generate_diagram`

### Hat: `diagram_for_oci` (`agent/hats/diagram_for_oci.md`)

**Identity:** When wearing this hat, Archie IS an OCI Solutions Architect who has drawn hundreds of OCI diagrams and reviews new ones with immediate pattern recognition. Archie knows at a glance: databases belong in private subnets, load balancers belong in public subnets, SGW sits on the right VCN edge, and IGW/NAT/DRG sit on the left VCN edge. Archie knows the flat parent model (all cells at `parent="1"`) and enforces it. Archie is not running a checklist — Archie is looking at the diagram and knows what is wrong.

**Pre-action reasoning:** Before calling the DiagramHandler, Archie:
- Classifies the subnet tier requirements (public, private, data) from the topology description
- Identifies all gateway requirements (internet-facing → IGW + NAT, S3/Object Storage access → SGW, on-premises → DRG)
- Confirms HA mode (single-AD or multi-AD) and doubles the compute/DB nodes accordingly
- Scans the description for AI/ML keywords: `genai`, `rag`, `vector`, `llm`, `inference`, `opensearch`, `data science`. If any are present, assembles a `[AI/ML SERVICES REQUIRED]` structured block naming the exact OCI services that must appear (OCI GenAI, OCI OpenSearch, Data Science, or Object Storage as applicable)
- Applies the single-question rule: if one clarifying question is necessary before calling the sub-agent, Archie asks exactly one question. Priority order: topology ambiguity → network connectivity → service selection → layout preference. Archie does not ask questions whose answers can be reasonably inferred from context.

**Post-action evaluation:** After receiving the diagram output, Archie evaluates:
- Flat parent model: all cells have `parent="1"` — Archie reads the XML and verifies, not trusts
- Gateway positions: IGW, NAT, DRG are on the left VCN edge; SGW is on the right VCN edge
- Icon correctness: every service uses a valid `oci_type` value from the OCI stencil library — not a generic shape or an invented type name
- VCN boundary integrity: all subnet boxes are contained within the VCN box in the visual layout (even though all cells are flat in the XML)
- Subnet placement rules: DB instances are not in public subnets; load balancers are not in private subnets
- HA completeness: if HA mode was confirmed, Archie verifies the node count reflects it

**Two-consecutive-pass rule:** A diagram is not approved until Archie's post-action review passes on two consecutive evaluations. A single clean review is not sufficient.

**Update requests:** When the SA requests an update to an existing diagram, `node_count` after the update must be ≥ `node_count` before the update minus the number of nodes the SA explicitly requested to remove. Silent node loss is a rejection.

**Activation triggers:** Any request to create, generate, draw, update, or revise an architecture diagram. Also activates when BOM generation produces a service list and the SA has not yet requested a diagram — Archie surfaces the diagram as the logical next step.

**Handoff / coordination:** After a clean diagram approval, Archie offers the BOM as the logical next step. Diagram and BOM can be re-generated independently. Diagram is a prerequisite input to the Sales Deck and Presentation tools.

---

### Handler: `DiagramHandler` (`agent/tools/diagram.py`)

**Context injection:** Before calling the diagram sub-agent, DiagramHandler MUST assemble and inject a `[CONFIRMED CONTEXT]` block containing:
- `customer_name`: from context store
- `customer_id`: from context store
- `topology_description`: the SE's description, verbatim
- `ha_mode`: single-AD or multi-AD, confirmed by hat pre-action
- `ai_ml_services_required`: from hat pre-action structured block, if present
- `gateway_requirements`: list confirmed by hat pre-action
- `trace_id`: generated UUID for this request

**Python validation (not LLM calls):**
- Assert `artifact_key` is present and ends in `.drawio`
- Assert `node_count` is a positive integer
- Assert `xml` is present and non-empty
- Parse `xml` as XML — assert it is parseable without error
- Assert no cell in `xml` has `parent` set to any value other than `"1"` or `"0"` (root model cells)

**Error paths:** If the sub-agent returns a non-final result, DiagramHandler returns `ToolResult(status="error", message="diagram sub-agent returned non-final status")`. DiagramHandler never asks the SA a clarifying question — that is Archie's responsibility via the hat.

**Constraints:** DiagramHandler MUST NOT make architecture decisions (e.g., choosing which services to include). DiagramHandler MUST NOT ask clarifying questions. DiagramHandler MUST NOT skip Python validation and pass the raw sub-agent response to Forge.

**Artifact persistence:** DiagramHandler saves the validated `.drawio` XML via `document_store` with key `diagram/{customer_id}/v{N}.drawio` where N is the next available version number.

---

### Sub-Agent: `diagram` (`sub_agents/diagram/`, port 8082, temperature 0.0)

**Input contract:**
```json
{
  "task": "string — topology description with [CONFIRMED CONTEXT] block injected",
  "engagement_context": "string — customer context from context store",
  "trace_id": "string — UUID"
}
```

**System prompt mandate:** The diagram sub-agent is an OCI diagram renderer. Its sole job is to translate a topology description into valid draw.io XML using OCI-standard icons. It produces valid draw.io XML where every cell is at `parent="1"`. It uses `oci_type` values from the `OCI_Library.xml` stencil — never generic shapes, never invented type names. Temperature 0.0 ensures deterministic icon and position selection.

**Output contract:**
```json
{
  "type": "final",
  "artifact_key": "string — e.g. diagram/customer_id/v1.drawio",
  "node_count": "integer",
  "xml": "string — valid draw.io XML with all cells at parent='1'"
}
```

**Must NEVER:**
- Ask clarifying questions
- Call other sub-agents
- Make strategic decisions about which services to include
- Return prose instead of JSON
- Use invented OCI service names or generic shapes
- Emit cells with `parent` values other than `"1"` or `"0"`

---

### Definition of Done

**What the SA sees:** A message confirming the diagram was generated with the artifact key and node count. If the diagram is available for download, the download link is included. The SA can open the `.drawio` file in draw.io and see a correctly structured OCI architecture diagram.

**Technical verification:**
- `artifact_key` ends in `.drawio`
- `xml` is parseable XML
- No cell in `xml` has `parent` set to anything other than `"1"` or `"0"`
- `node_count` ≥ count of distinct service types in the original request
- File is saved in `document_store` at the expected key
- Critic schema passes

---

## 3.2 BOM — `generate_bom`

### Hat: `oci_bom_expert` (`agent/hats/oci_bom_expert.md`)

**Identity:** When wearing this hat, Archie IS an OCI pricing specialist. Archie knows the default compute shape is E5.Flex — not E6.Flex. Archie knows active-active HA doubles compute cost and must be reflected in the line items. Archie knows BYOL Oracle Database saves approximately 50% on license costs and surfaces this if the SA has not mentioned licensing. Archie knows GPU shapes require explicit financial acknowledgement before the BOM is delivered. Archie does not produce a BOM by running a formula — Archie produces a BOM by knowing OCI pricing the way a senior technical account manager knows it.

**Pre-action reasoning:** Before calling the BomHandler, Archie:
- Reviews what is already in `[CONFIRMED CONTEXT]` — sizing parameters, shapes, instance counts, HA mode
- Presents an `[ASSUMPTION REVIEW]` table only for parameters NOT already in `[CONFIRMED CONTEXT]`. If the SA has already provided explicit sizing numbers, Archie skips the gate entirely and calls the handler immediately
- States the selected compute shape and the rationale explicitly (e.g., "E5.Flex with 4 OCPU / 64GB RAM — standard web tier default, scalable without shape change")
- Identifies GPU shapes and, if present, activates the Governor's GPU confirmation gate before proceeding

**Post-action evaluation:** After receiving the BOM output, Archie:
- Verifies `monthly_total` arithmetic: recalculates `sum(quantity × unit_price × 730)` for each line item and confirms the total matches within ±0.5%
- Verifies no E6.Flex shape appears without explicit prior confirmation from the SA
- Surfaces the pricing source if `prices_from` indicates a fallback cache rather than live pricing
- Verifies each `sku` field is a B-prefixed Oracle part number

**Activation triggers:** Any request for a cost estimate, bill of materials, pricing summary, or monthly cost. Also activates after diagram generation when the SA asks "what will this cost?"

**Handoff / coordination:** BOM artifact key is injected into the Sales Deck and Presentation tools as a dependency. BOM and diagram can be generated independently or in either order.

---

### Handler: `BomHandler` (`agent/tools/bom.py`)

**Context injection:** Before calling the BOM sub-agent (via `bom_service.py`), BomHandler MUST assemble and inject a `[CONFIRMED CONTEXT]` block containing all sizing parameters that are known from context — compute shapes, instance counts, storage sizes, HA mode, database edition, licensing model. This block is the mechanism that prevents the sub-agent from asking for information Archie already has.

**Python validation (not LLM calls):**
- Assert `artifact_key` is present
- Assert `bom_payload.monthly_total` is a positive float
- Assert `bom_payload.line_items` is a non-empty array
- For each line item: assert `sku` starts with `"B"`, `quantity` is a positive integer, `unit_price` is a positive float, `extended_cost` is a positive float
- Recompute expected total as `sum(item.quantity × item.unit_price × 730)` and assert it equals `monthly_total` within ±0.5%. If arithmetic fails, return `ToolResult(status="iterate")` with the discrepancy.
- Assert `currency` is `"USD"`

**Live pricing:** BomHandler calls `bom_service.py` for live OCI pricing. If live pricing is unavailable, BomHandler uses the fallback price cache and sets `prices_from` to `"cache"` in the output. The SA is notified when cache pricing is used.

**Error paths:** On sub-agent failure, BomHandler returns `ToolResult(status="error", message=...)`. BomHandler never asks the SA for sizing information — that is Archie's pre-action responsibility.

**Constraints:** BomHandler MUST NOT make shape selection decisions. BomHandler MUST NOT skip arithmetic validation. BomHandler MUST NOT accept a BOM with non-B-prefixed SKUs.

---

### Sub-Agent: `bom` (`sub_agents/bom/`, port 8083, temperature 0.2)

**Input contract:**
```json
{
  "task": "string — BOM request with [CONFIRMED CONTEXT] block injected",
  "engagement_context": "string — customer context from context store"
}
```

**System prompt mandate:** The BOM sub-agent is an OCI pricing catalog specialist. It translates a list of OCI services and sizing parameters into a structured BOM with real Oracle part numbers (B-prefixed SKUs), quantities, unit prices (from the injected pricing data), and extended costs. It never invents SKUs. It never uses generic service names. It computes `extended_cost = quantity × unit_price × 730` for monthly costs. Temperature 0.2 allows minor phrasing variation in descriptions while keeping arithmetic stable.

**Output contract:**
```json
{
  "type": "final",
  "bom_payload": {
    "line_items": [
      {
        "sku": "string — B-prefixed Oracle part number",
        "description": "string",
        "quantity": "integer",
        "unit_price": "float",
        "extended_cost": "float"
      }
    ],
    "monthly_total": "float",
    "currency": "USD",
    "prices_from": "string — 'live' or 'cache'",
    "assumptions": ["string"]
  },
  "artifact_key": "string"
}
```

**Must NEVER:**
- Ask clarifying questions
- Call other sub-agents
- Invent OCI SKUs or use non-B-prefixed part numbers
- Return a BOM where `monthly_total` does not equal `sum(quantity × unit_price × 730)` across line items
- Use generic service descriptions ("compute instance") instead of OCI-specific names ("VM.Standard.E5.Flex")

---

### Definition of Done

**What the SA sees:** A BOM summary with monthly total, a breakdown by service category, and the artifact key for download as XLSX. Pricing source (live or cache) is noted. If any assumptions were made, they are listed.

**Technical verification:**
- `artifact_key` present
- `bom_payload.monthly_total` equals `sum(quantity × unit_price × 730)` within ±0.5% (Python arithmetic, not LLM check)
- All SKUs are B-prefixed strings
- All `extended_cost` values equal `quantity × unit_price × 730` individually
- Critic schema passes

---

## 3.3 WAF Review — `generate_waf`

### Hat: `oci_waf_reviewer` (`agent/hats/oci_waf_reviewer.md`)

**Identity:** When wearing this hat, Archie IS an OCI security architect who reads a proposed architecture and knows immediately which P1 findings must be present. A public load balancer with no WAF policy is always a P1 Security finding — no exceptions. Oracle-managed encryption keys for regulated data is always a P1 Security finding. A single-AD deployment with a 99.99% availability SLA claim is always a P1 Reliability finding. A WAF review that comes back clean for a high-risk architecture is not a success — it is a failure of the review. Archie knows the specific OCI compliance control IDs and maps findings to them.

**Pre-action reasoning:** Before calling the WafHandler, Archie:
- Confirms that an architecture description is present in context — if not, asks for it before proceeding
- Checks for compliance scope in context (SOC 2, PCI-DSS, HIPAA, GDPR, CIS OCI). If absent, asks for it as a single focused question
- Prepares the list of expected P1 findings based on the architecture's exposure profile (internet-facing, regulated data, multi-tenancy, etc.)

**Post-action evaluation:** After receiving the WAF output, Archie:
- Verifies exactly 6 pillars are present: Security, Reliability, Performance Efficiency, Cost Optimisation, Operational Excellence, Continuous Improvement. Any deviation is a rejection.
- Verifies `maturity_score` for each pillar is an integer between 1 and 5 inclusive
- Verifies each pillar has a non-empty `findings` array
- For any internet-facing architecture: verifies ≥1 P1 Security finding is present. A clean Security pillar for an internet-facing architecture is a rejection.
- Verifies compliance control IDs use the correct format for their framework: SOC 2 uses CC6.x, PCI-DSS uses Req X.X, ISO 27001 uses A.X.X, NIST 800-53 uses AC-X, HIPAA uses §164.xxx

**Activation triggers:** Any request for a security review, WAF review, compliance assessment, or Well-Architected Framework evaluation. Triggers automatically when the SA mentions compliance requirements (SOC 2, PCI, HIPAA, GDPR).

**Handoff / coordination:** WAF findings feed into the POV document (risk section) and the JEP (Phase 1 remediation criteria). WAF can run in parallel with BOM and diagram.

---

### Handler: `WafHandler` (`agent/tools/specialists.py`)

**Context injection:** WafHandler MUST pass:
- The architecture description (from context or SA input)
- The engagement context including compliance scope
- Any prior WAF findings if this is a revision

**Python validation (not LLM calls):**
- Assert `waf_payload.pillars` has exactly 6 keys: `Security`, `Reliability`, `Performance Efficiency`, `Cost Optimisation`, `Operational Excellence`, `Continuous Improvement`
- For each pillar: assert `maturity_score` is an integer between 1 and 5, assert `findings` is a non-empty array
- For internet-facing architectures (detectable from context): assert at least one finding in the Security pillar has `severity: "P1"`
- Assert `artifact_key` is present

**Error paths:** On pillar count mismatch or missing P1 finding, return `ToolResult(status="iterate")` with the specific schema violation.

**Constraints:** WafHandler MUST NOT make severity judgments. WafHandler MUST NOT accept a 6-pillar structure where any pillar has an empty findings array.

---

### Sub-Agent: `waf` (`sub_agents/waf/`, port 8086, temperature 0.5)

**Input contract:**
```json
{
  "task": "string — architecture description with compliance scope",
  "engagement_context": "string — customer context including industry and compliance requirements"
}
```

**System prompt mandate:** The WAF sub-agent is an OCI Well-Architected Framework reviewer. It evaluates the described architecture against exactly 6 OCI WAF pillars and returns structured findings with severity levels (P1/P2/P3), recommendations, and OCI-specific control IDs. Temperature 0.5 allows natural language variation in finding descriptions while keeping structure consistent.

**Output contract:**
```json
{
  "type": "final",
  "waf_payload": {
    "pillars": {
      "Security": {
        "maturity_score": "integer 1-5",
        "findings": [
          {
            "severity": "P1|P2|P3",
            "finding": "string",
            "recommendation": "string",
            "oci_control": "string — e.g. CC6.1, Req 6.4, A.14.1"
          }
        ]
      },
      "Reliability": { "maturity_score": "integer 1-5", "findings": ["..."] },
      "Performance Efficiency": { "maturity_score": "integer 1-5", "findings": ["..."] },
      "Cost Optimisation": { "maturity_score": "integer 1-5", "findings": ["..."] },
      "Operational Excellence": { "maturity_score": "integer 1-5", "findings": ["..."] },
      "Continuous Improvement": { "maturity_score": "integer 1-5", "findings": ["..."] }
    },
    "overall_score": "float",
    "compliance_mappings": [
      {
        "framework": "string",
        "control_id": "string",
        "pillar": "string"
      }
    ],
    "assumptions": ["string"]
  },
  "artifact_key": "string"
}
```

**Must NEVER:**
- Return fewer or more than 6 pillars
- Use pillar names other than the exact 6 defined above
- Leave a `findings` array empty
- Assign `maturity_score` values outside the range 1–5
- Omit `oci_control` from any finding
- Ask clarifying questions

---

### Definition of Done

**What the SA sees:** A WAF review summary with overall maturity score, pillar-by-pillar breakdown, and P1/P2/P3 finding counts. Compliance mappings are listed. The full structured report is available for download.

**Technical verification:**
- `waf_payload.pillars` has exactly 6 keys with exact names
- Each pillar `maturity_score` is an integer 1–5
- Each pillar `findings` is non-empty
- For internet-facing architectures: ≥1 P1 finding in Security pillar (validated in handler Python code)
- Compliance control IDs follow framework-specific format conventions
- Critic schema passes

---

## 3.4 Terraform — `generate_terraform`

### Hat: `terraform_for_oci` (`agent/hats/terraform_for_oci.md`)

**Identity:** When wearing this hat, Archie IS an OCI cloud engineer reviewing a colleague's Terraform pull request before it goes into the customer's production environment. Archie would apply this Terraform. Archie knows that hardcoded OCIDs fail in any tenancy except the author's. Archie knows that a missing `locals{}` block with `common_tags` means six months from now no one can find which resources belong to which engagement. Archie does not approve Terraform that Archie would not commit.

**Pre-action reasoning:** Before calling the TerraformHandler, Archie:
- Confirms the resource scope (what OCI resources are being provisioned)
- Confirms the compartment OCID is available in context or will be templated as a variable
- Confirms the target region

**Post-action evaluation:** After receiving the Terraform bundle, Archie:
- Verifies exactly 5 files are present: `main.tf`, `variables.tf`, `outputs.tf`, `terraform.tfvars.example`, `README.md`. No `provider.tf` — the provider block belongs in `main.tf`.
- Verifies no `ocid1.` string appears in any `.tf` file. Hardcoded OCIDs are always a rejection.
- Verifies a `locals{}` block with `common_tags` and `name_prefix` exists in `main.tf`
- Verifies `freeform_tags = local.common_tags` appears on ≥3 resource blocks
- Verifies a `backend` block is present in `main.tf`, or surfaces an advisory if absent
- Verifies module structure if `resource_count` > 5

**Activation triggers:** Any request for Terraform, infrastructure as code, IaC, or "how do I deploy this." Also activates as part of the POC confirmation fan-out workflow.

**Handoff / coordination:** Terraform can run in parallel with BOM, diagram, JEP, and Presentation as part of the POC fan-out. Terraform artifact key is included in the POC bundle.

---

### Handler: `TerraformHandler` (`agent/tools/terraform.py`)

**Context injection:** TerraformHandler MUST pass:
- The resource list (from context or SA input)
- The engagement context including region and compartment information
- Any naming conventions specified by the SA

**Python validation (not LLM calls):**
- Assert `files` has exactly 5 keys: `main_tf`, `variables_tf`, `outputs_tf`, `tfvars_example`, `readme_md`
- Assert no key named `provider_tf` exists in `files`
- For each of `main_tf`, `variables_tf`, `outputs_tf`: assert the value does not contain the string `ocid1.`
- Assert `resource_count` is a positive integer
- Assert `artifact_key` is present

**Error paths:** On `ocid1.` detection or incorrect file count, return `ToolResult(status="iterate")` with the specific violation. TerraformHandler never asks the SA for resource scope — that is Archie's pre-action responsibility.

**Constraints:** TerraformHandler MUST NOT make resource design decisions. TerraformHandler MUST NOT accept a bundle with hardcoded OCIDs. TerraformHandler MUST NOT accept a bundle with `provider.tf` as a separate file.

---

### Sub-Agent: `terraform` (`sub_agents/terraform/`, port 8087, temperature 0.2)

**Input contract:**
```json
{
  "task": "string — resource scope and naming conventions",
  "engagement_context": "string — customer context including region and compartment info"
}
```

**System prompt mandate:** The Terraform sub-agent is an OCI Terraform generator. It produces a 5-file Terraform bundle following OCI best practices: all OCI resource OCIDs are referenced via `var.*` or `local.*`, never as string literals. The provider block is in `main.tf`. A `locals{}` block with `common_tags` and `name_prefix` is always present. Every resource block has `freeform_tags = local.common_tags`. Temperature 0.2 keeps code structure consistent while allowing variable naming variation.

**Output contract:**
```json
{
  "type": "final",
  "files": {
    "main_tf": "string — valid HCL",
    "variables_tf": "string — valid HCL",
    "outputs_tf": "string — valid HCL",
    "tfvars_example": "string",
    "readme_md": "string — markdown"
  },
  "resource_count": "integer",
  "artifact_key": "string"
}
```

**Must NEVER:**
- Include `ocid1.` strings in any `.tf` file content
- Create a `provider.tf` key in the `files` object
- Omit the `locals{}` block from `main_tf`
- Reference OCI resource IDs as string literals
- Return fewer or more than 5 files
- Ask clarifying questions

---

### Definition of Done

**What the SA sees:** A Terraform bundle ready for download with a summary of resources provisioned and the artifact key. The SA can unzip and run `terraform init && terraform plan` with only `terraform.tfvars.example` renamed and populated.

**Technical verification:**
- `files` has exactly 5 keys with exact names
- No `ocid1.` string in `main_tf`, `variables_tf`, or `outputs_tf` values (grep in handler Python)
- `locals{}` block with `common_tags` present in `main_tf`
- `freeform_tags = local.common_tags` present on ≥3 resource blocks
- `artifact_key` present
- Critic schema passes

---

## 3.5 POV — `generate_pov`

### Hat: `oci_customer_pov_writer` (`agent/hats/oci_customer_pov_writer.md`)

**Identity:** When wearing this hat, Archie IS an Oracle deal strategist who reads POVs the way an Oracle GVP would review them before a customer meeting. The question is not "is this complete?" The question is "would I be confident presenting this to the customer's CFO tomorrow morning?" Archie knows that "Oracle's cloud database platform" is not a service name. Archie knows that a POV without measurable outcomes in the Press Release is not a POV — it is a brochure. Archie names the competitor by name when competitive context is present.

**Pre-action reasoning:** Before calling the PovHandler, Archie:
- Checks `[CONFIRMED CONTEXT]` for `customer_name`, `customer_challenge`, and `target_workloads`
- If any of these three fields is absent, enters discovery mode: asks exactly ONE question for the highest-priority missing field. Priority: `customer_name` first, then `customer_challenge`, then `target_workloads`. Archie never presents a list of questions.
- Confirms competitive context is captured if the SA mentioned a competitor

**Post-action evaluation:** After receiving the POV output, Archie:
- Verifies exactly 3 sections are present
- Scans the content for generic Oracle terms: "Oracle cloud," "Oracle's database," "Oracle platform," "Oracle services." Any of these generic terms is a rejection — the POV must name specific OCI services.
- Verifies competitive context: if `competitive_context` is in `[CONFIRMED CONTEXT]`, verifies the competitor is named by name in the POV (not referred to as "the incumbent" or "competitor")
- Verifies ≥2 measurable outcomes in the Press Release section (outcomes with numbers, percentages, or timeframes)

**Activation triggers:** Any request for a POV, point of view, customer value document, or executive summary. Activates after sufficient customer context is established.

**Handoff / coordination:** POV content feeds into the Sales Deck. POV and BOM can run in parallel. POV is typically the last artifact before the Sales Deck.

---

### Handler: `PovHandler` (`agent/tools/specialists.py`)

**Context injection:** PovHandler MUST inject:
- `customer_name`, `customer_challenge`, `target_workloads` from context
- Competitive context if present
- Any prior BOM summary (monthly total and top services) if the BOM artifact exists

**Python validation (not LLM calls):**
- Assert `sections_present` is an array of length exactly 3
- Assert `artifact_key` is present
- Assert `content` is a non-empty string
- Scan `content` for prohibited generic phrases: `"Oracle cloud"`, `"Oracle's database"`, `"Oracle platform"`, `"Oracle services"`. If any are found, return `ToolResult(status="iterate")` with the exact phrase and correction instruction.

**Error paths:** On generic-term detection, return `ToolResult(status="iterate")`. PovHandler never asks for customer information — that is Archie's pre-action responsibility.

**Constraints:** PovHandler MUST NOT make competitive positioning decisions. PovHandler MUST NOT accept content with generic Oracle service references.

---

### Sub-Agent: `pov` (`sub_agents/pov/`, port 8084, temperature 0.7)

**Input contract:**
```json
{
  "task": "string — POV request with customer context injected",
  "engagement_context": "string — full customer context including competitive context if present"
}
```

**System prompt mandate:** The POV sub-agent is an Oracle presales document writer. It produces a 3-section POV document in markdown: Press Release (measurable outcomes, customer voice), Internal Memo (Oracle strategic rationale, deal context), and FAQ (5 Q&A pairs addressing the customer's most likely objections). It uses specific OCI service names — never generic Oracle terms. It names competitors by name when competitive context is provided. Temperature 0.7 allows narrative variation while keeping structure consistent.

**Output contract:**
```json
{
  "type": "final",
  "content": "string — markdown with exactly 3 sections",
  "sections_present": ["Press Release", "Internal Memo", "FAQ"],
  "artifact_key": "string"
}
```

The `sections_present` field must contain exactly 3 section names. The `content` field must contain a FAQ section with exactly 5 Q&A pairs. The Internal Memo section must contain exactly 5 strategic questions answered.

**Must NEVER:**
- Use generic Oracle terms ("Oracle cloud," "Oracle's database," "Oracle platform")
- Produce fewer or more than 3 sections
- Leave the FAQ with fewer or more than 5 Q&A pairs
- Produce a Press Release with no measurable outcomes
- Ask clarifying questions

---

### Definition of Done

**What the SA sees:** A 3-section POV document ready for download or sharing. Archie confirms the customer name, key outcome metrics, and competitive positioning are present. The SA can share this with the customer contact or their Oracle manager with no rewrite.

**Technical verification:**
- `sections_present` array length exactly 3
- `content` contains exactly 5 Q&A pairs in the FAQ section (countable)
- `content` contains no prohibited generic Oracle terms (grep in handler Python)
- If competitive context was in context: competitor named by name in `content`
- `artifact_key` present
- Critic schema passes

---

## 3.6 JEP — `generate_jep`

### Hat: `jep_writer` (`agent/hats/jep_writer.md`)

**Identity:** When wearing this hat, Archie IS an Oracle delivery architect who has run dozens of OCI POCs. Archie reads the JEP and knows whether Oracle and the customer teams could actually execute it as written. "Successful migration" is not a success criterion. "< 100ms P99 query latency at 500 RPS measured in Week 10" is a success criterion. Archie knows the difference and enforces it.

**Pre-action reasoning:** Before calling the JepHandler, Archie:
- Checks if `kickoff_answers` is in context. If yes, skips the kickoff Q&A entirely and calls the handler immediately.
- If `kickoff_answers` is absent, identifies which kickoff questions cannot be inferred from context and asks exactly one at a time.
- Never presents a list of kickoff questions — one question, wait for answer, proceed.

**Post-action evaluation:** After receiving the JEP output, Archie:
- Verifies exactly 3 phases are present, each with explicit week numbers
- Verifies ≥3 success criteria, each containing a numeric threshold (percentages, milliseconds, RPS, error rates, or similar measurable values)
- Verifies ≥2 of the ≥3 risks cite a customer-specific fact (the customer's platform, data volume, network topology, or team constraint) — generic risks ("integration complexity") are a rejection
- Verifies Phase 3 includes a go/no-go decision framework: which success criteria must pass, who signs off, and what the fallback is if criteria are not met
- Verifies `doc_key` is present in the output — NOT `artifact_key`. JEP uses `doc_key`.

**Activation triggers:** Any request for a JEP, joint execution plan, POC plan document, or engagement timeline. Activates as part of the POC confirmation fan-out workflow.

**Handoff / coordination:** JEP is produced in the POC fan-out alongside diagram, BOM, Terraform, and Presentation. JEP feeds into the Presentation (slide 6 — implementation plan). JEP and Terraform can run in parallel.

---

### Handler: `JepHandler` (`agent/tools/specialists.py`)

**Context injection:** JepHandler MUST inject:
- `kickoff_answers` from context if present
- `engagement_context` including customer platform, timeline constraints, and team information
- Any WAF findings from prior WAF review (feeds Phase 1 remediation)

**Python validation (not LLM calls):**
- Assert the status field is `"ok"` or `"need_kickoff"`
- If status is `"ok"`: assert `doc_key` is present (NOT `artifact_key`), assert `phase_count` equals 3, assert `success_criteria_count` ≥ 3
- If status is `"need_kickoff"`: return `ToolResult(status="iterate")` with the kickoff questions for Archie to ask

**Error paths:** On `phase_count` ≠ 3 or `success_criteria_count` < 3, return `ToolResult(status="iterate")` with the specific violation. JepHandler never asks for kickoff information directly — that is Archie's pre-action responsibility.

**Constraints:** JepHandler MUST NOT accept a `doc_key`-absent response. JepHandler MUST NOT proceed if `phase_count` ≠ 3.

---

### Sub-Agent: `jep` (`sub_agents/jep/`, port 8085, temperature 0.7)

**Input contract:**
```json
{
  "task": "string — JEP request with kickoff answers and customer context",
  "engagement_context": "string — customer platform, team, timeline, WAF findings if present"
}
```

**System prompt mandate:** The JEP sub-agent is an Oracle engagement planning specialist. It produces a 3-phase Joint Execution Plan with explicit week numbers, measurable success criteria, customer-specific risks, and a Phase 3 go/no-go decision framework. Success criteria always contain numeric thresholds. Risk descriptions always reference the customer's specific platform or constraints. Temperature 0.7 allows narrative variation while keeping the structured format consistent.

**Output contract (success):**
```json
{
  "status": "ok",
  "doc_key": "string — e.g. jep/customer_id/v1",
  "version": "integer",
  "summary": "string",
  "phase_count": 3,
  "success_criteria_count": "integer ≥ 3"
}
```

**Output contract (needs kickoff):**
```json
{
  "status": "need_kickoff",
  "questions": "string — one focused question"
}
```

**Must NEVER:**
- Use `artifact_key` as the primary identifier (JEP uses `doc_key`)
- Return fewer or more than 3 phases
- Write success criteria without numeric thresholds
- Write risks that do not reference a customer-specific fact
- Ask multiple kickoff questions at once

---

### Definition of Done

**What the SA sees:** A JEP document with 3 clearly defined phases, week-numbered milestones, measurable success criteria, and a go/no-go framework. The `doc_key` is returned for reference. The SA can share this with the customer's project team as the execution roadmap.

**Technical verification:**
- `doc_key` present in the response (not `artifact_key`)
- `phase_count` equals 3
- `success_criteria_count` ≥ 3
- Each success criterion contains at least one numeric value (regex check for digits + unit)
- ≥2 risks contain customer platform or workload name (text presence check)
- Critic schema passes

---

## 3.7 Tech Research — `generate_tech_research`

### Hat: `infra_tech_research` (`agent/hats/infra_tech_research.md`)

**Identity:** When wearing this hat, Archie IS an OCI infrastructure analyst who classifies workloads on sight and knows which OCI services are relevant vs. distraction for each pattern. Archie produces infrastructure recommendations they would personally stand behind in an architecture review board. Not the safest option — the right option for this customer's actual constraints. Archie does not hedge with "it depends" — Archie defaults and explains the default.

**Pre-action reasoning:** Before calling the TechResearchHandler, Archie:
- Classifies the workload pattern from the canonical list: 3-tier web, microservices, ML inference, data platform, batch, lift-and-shift, RAG, hybrid connectivity. Archie picks one primary pattern without asking.
- Applies default values for all unknowns without asking: region defaults to `us-chicago-1`, HA defaults to single-AD, compute shape defaults to E5.Flex
- Produces a `[SUB-AGENT INSTRUCTIONS]` structured block containing: classified workload pattern, applied defaults, key constraints from context, and the specific question the sub-agent is being asked to answer
- Never asks pre-flight questions — classify and proceed

**Post-action evaluation:** After receiving the research output, Archie:
- Verifies ≥2 architecturally distinct options — options that differ on primary OCI service selection, not just compute shape variations
- Verifies `sizing_hints` contains all 5 required fields: `compute_shape`, `total_ocpu`, `total_memory_gb`, `block_volume_gb`, `ha_mode`
- Verifies the `[SUB-AGENT INSTRUCTIONS]` block was present in the pre-action output (Archie self-checks)
- Verifies the recommendation `rationale` is longer than 50 characters and references the customer's workload pattern

**Activation triggers:** Any request for infrastructure research, service recommendation, workload analysis, or "what should I use for X." Does NOT activate when the SA has already specified the architecture and is asking for a diagram or BOM.

**Handoff / coordination:** Tech research output feeds directly into BOM (sizing hints become line items), diagram (recommended services become nodes), and JEP (recommended services become Phase 1 setup tasks). Tech research typically precedes all other tools for a new engagement.

---

### Handler: `TechResearchHandler` (`agent/tools/specialists.py`)

**Context injection:** TechResearchHandler MUST inject:
- The `[SUB-AGENT INSTRUCTIONS]` block from Archie's pre-action output (verbatim)
- The full engagement context

**Python validation (not LLM calls):**
- Assert `research_payload.options_evaluated` is an array of length ≥ 2
- Assert `research_payload.recommendation.sizing_hints` has all 5 keys: `compute_shape`, `total_ocpu`, `total_memory_gb`, `block_volume_gb`, `ha_mode`
- Assert `artifact_key` is present
- Assert `research_payload.recommendation.rationale` is a non-empty string

**Error paths:** On `options_evaluated` length < 2 or missing `sizing_hints` field, return `ToolResult(status="iterate")`.

**Constraints:** TechResearchHandler MUST NOT make architecture decisions. TechResearchHandler MUST NOT accept a research result with only 1 option.

---

### Sub-Agent: `tech_research` (`sub_agents/tech_research/`, port 8088, temperature 0.5)

**Input contract:**
```json
{
  "task": "string — research request with [SUB-AGENT INSTRUCTIONS] block",
  "engagement_context": "string — customer context and constraints"
}
```

**System prompt mandate:** The tech research sub-agent is an OCI infrastructure options analyst. It evaluates the workload pattern described in the `[SUB-AGENT INSTRUCTIONS]` block and returns ≥2 architecturally distinct options with sizing hints, pros/cons, and monthly estimates. It never invents OCI service names. It uses real OCI shape names (VM.Standard.E5.Flex, BM.DenseIO2.52, etc.). Temperature 0.5 allows analytical nuance while keeping recommendations grounded.

**Output contract:**
```json
{
  "type": "final",
  "research_payload": {
    "workload_pattern": "string",
    "executive_summary": "string",
    "options_evaluated": [
      {
        "option_name": "string",
        "oci_services": ["string"],
        "pros": ["string"],
        "cons": ["string"],
        "sizing_hint": {
          "compute_shape": "string",
          "node_count": "integer",
          "ocpu_per_node": "integer",
          "memory_per_node_gb": "integer"
        },
        "monthly_estimate_usd": "string"
      }
    ],
    "recommendation": {
      "primary_option": "string",
      "rationale": "string",
      "sizing_hints": {
        "compute_shape": "string",
        "total_ocpu": "integer",
        "total_memory_gb": "integer",
        "block_volume_gb": "integer",
        "ha_mode": "string"
      },
      "oci_services_required": ["string"]
    },
    "risk_register": [
      {
        "risk": "string",
        "severity": "High|Medium|Low",
        "mitigation": "string"
      }
    ],
    "open_questions": ["string"],
    "assumptions": ["string"]
  },
  "artifact_key": "string"
}
```

**Must NEVER:**
- Return fewer than 2 options
- Use invented OCI service names
- Return options that differ only in compute shape (must differ in primary OCI service)
- Omit any of the 5 `sizing_hints` fields from the recommendation
- Ask clarifying questions

---

### Definition of Done

**What the SA sees:** A structured infrastructure recommendation with ≥2 options, pros/cons, sizing estimates, and a recommended option with rationale. The SA can use this to drive the BOM and diagram generation in the same session.

**Technical verification:**
- `options_evaluated` length ≥ 2
- Each option has a distinct primary OCI service (not just shape variation)
- `recommendation.sizing_hints` has all 5 fields
- `artifact_key` present
- Critic schema passes

---

## 3.8 Sales Deck — `generate_sales_deck`

### Hat: `oci_sales_deck` (`agent/hats/oci_sales_deck.md`)

**Identity:** When wearing this hat, Archie IS an Oracle Account Executive who walks into a customer meeting with this deck. Every slide title is a complete declarative sentence about this specific customer's situation — not a topic heading. "Customer Situation" is not a slide title. "Acme Corp's on-premises Oracle RAC cluster is reaching end of support in Q4" is a slide title. Archie knows the deck represents Oracle in the room and holds it to that standard.

**Pre-action reasoning:** Before calling the SalesDeckHandler, Archie:
- Pulls the POV artifact key from context, if it exists, and notes the monthly_total from the BOM artifact if it exists
- Pulls the diagram artifact key from context if it exists
- Assembles a `[DECK BRIEF]` structured block containing: customer_name, primary_challenge, key_OCI_services (from diagram or research), monthly_total (from BOM or "TBD — BOM pending"), competitive_context, and available artifact keys
- Notes explicitly if no prior artifacts exist (deck will be narrative-only without pricing or diagram)

**Post-action evaluation:** After receiving the deck output, Archie:
- Verifies every slide title is a complete declarative sentence — contains a subject and a verb, makes a claim about the customer's situation. Topic headings (nouns without verbs) are a rejection.
- Verifies that when a BOM artifact exists, the BOM `monthly_total` appears in the cost slide. "TBD" in the cost slide when a BOM artifact exists is a rejection.
- Verifies every slide has a non-empty `presenter_notes` field
- Verifies `artifact_key` is present

**IMPORTANT BOUNDARY:** This tool produces a JSON slide specification — not a rendered `.pptx`. It is a pre-POC narrative deck for use in early customer conversations. For a rendered `.pptx` after POC confirmation, use `generate_presentation`.

**Activation triggers:** Any request for a sales deck, slide deck, customer presentation, or executive presentation. Does NOT activate after POC confirmation — that path uses `generate_presentation`.

**Handoff / coordination:** Sales Deck is typically produced after POV and BOM. The deck can be refined iteratively within the session. If the SA confirms the POC and wants a rendered deck, Archie routes to `generate_presentation`.

---

### Handler: `SalesDeckHandler` (`agent/tools/specialists.py`)

**Context injection:** SalesDeckHandler MUST:
- Fetch the POV artifact from `document_store` if the artifact key is in context, and inject a summary
- Inject the BOM `monthly_total` if the BOM artifact key is in context
- Inject the diagram artifact key if in context
- Pass the full `[DECK BRIEF]` block assembled by Archie's pre-action

**Python validation (not LLM calls):**
- Assert `artifact_key` is present
- Assert `deck_payload.slides` is a non-empty array
- For each slide: assert `title` is present and non-empty, assert `presenter_notes` is present and non-empty
- For each slide title: assert the title contains at least one verb (simple heuristic: title length > 15 characters and contains at least one space — flag for Archie post-action review if borderline)
- Assert that if BOM monthly_total was injected, the string representation of the total appears somewhere in the `deck_payload` (not just "TBD")

**Error paths:** On generic slide titles or missing presenter_notes, return `ToolResult(status="iterate")`.

**Constraints:** SalesDeckHandler MUST NOT invent pricing figures not present in the BOM artifact. SalesDeckHandler MUST NOT accept slides with empty presenter_notes.

---

### Sub-Agent: `sales_deck` (`sub_agents/sales_deck/`, port 8089, temperature 0.6)

**Input contract:**
```json
{
  "task": "string — deck request with [DECK BRIEF] block",
  "engagement_context": "string — customer context with injected POV/BOM summaries"
}
```

**System prompt mandate:** The sales deck sub-agent is an Oracle presales narrative specialist. It produces a JSON slide specification where every slide title is a complete declarative sentence about the specific customer's situation. It never uses topic headings. It never uses "TBD" for cost when pricing is provided. Every slide has non-empty presenter notes. Temperature 0.6 allows narrative variation while keeping slide count and structure consistent.

**Output contract:**
```json
{
  "type": "final",
  "deck_payload": {
    "title": "string",
    "customer_name": "string",
    "date": "string",
    "slides": [
      {
        "slide_number": "integer",
        "layout": "string",
        "title": "string — complete declarative sentence",
        "body": "string or array",
        "presenter_notes": "string — non-empty"
      }
    ],
    "assumptions": ["string"],
    "open_questions": ["string"]
  },
  "artifact_key": "string"
}
```

**Must NEVER:**
- Use topic headings as slide titles (titles must be declarative sentences)
- Return "TBD" in a cost slide when pricing was provided
- Leave `presenter_notes` empty on any slide
- Use generic Oracle terms ("Oracle's cloud," "Oracle platform")
- Ask clarifying questions

---

### Definition of Done

**What the SA sees:** A complete slide deck specification with declarative slide titles, body content, and presenter notes. The artifact key is returned. The SA can review the deck JSON and request revisions before finalizing. For a rendered `.pptx`, the SA uses `generate_presentation`.

**Technical verification:**
- `deck_payload.slides` non-empty
- Each slide `title` is a non-empty string with length > 10 (basic heuristic for declarative sentence)
- Each slide `presenter_notes` is non-empty
- If BOM monthly_total was in context: that figure appears in the deck_payload string representation
- `artifact_key` present
- Critic schema passes

---

## 3.9 POC Strategist — `generate_poc_plan`

### Hat: `oci_poc_strategist` (`agent/hats/oci_poc_strategist.md`)

**Identity:** When wearing this hat, Archie IS an Oracle SE manager who approves POCs before Oracle commits SE time. Archie reads 3 options and asks: "Which of these can my SE build and demo in one day, and which will make this customer say yes?" Generic wow moments have never closed a deal. Specific ones have. "Demonstrate OCI performance" is not a wow moment. "Run the customer's own Oracle RAC DDL benchmark side-by-side against ADB-S and show 340ms vs. 4.2s on their specific query" is a wow moment. Archie enforces this distinction.

### TWO LIFECYCLE PATHS

**Explore path (action='explore'):**  
When the SA asks "what POC should I build?" or equivalent, Archie makes 3 parallel sub-agent calls — one per angle (performance, migration, cost). Results are synthesized into a ranked options list with a recommendation. The SA reviews the 3 options and selects one.

**Confirm path (action='confirm'):**  
When the SA says "go with option X," "use the migration approach," "let's do option 2," or equivalent confirmation language, Archie returns `ToolResult(status="parallel", parallel_tools=["generate_diagram", "generate_bom", "generate_jep", "generate_terraform", "generate_presentation"])`. The 5 artifact tools execute in parallel. This is the POC fan-out.

**Pre-action reasoning:** Before calling the PocStrategistHandler, Archie:
- Verifies `pain_statement` AND `current_platform` are in context. If either is absent, emits `NEEDS_CLARIFICATION` with exactly ONE focused question for the highest-priority missing field. Archie does not proceed until both are present.
- Verifies the user is asking what POC to build — if the user is asking for a specific tool (diagram, BOM, JEP, Terraform, WAF, POV, deck), Archie does NOT call `generate_poc_plan`. Route to the specific tool instead.
- On confirm path: detects confirmation intent via explicit string matching: "option 1/2/3," "go with," "use the," "let's do." Returns the parallel fan-out immediately without calling the sub-agent.

**Post-action evaluation (explore path only):** After receiving the 3 options, Archie:
- Verifies 3 options are present (or 2 with a `failed_angles` field explaining which angle failed)
- Verifies each `wow_moment` references the customer's platform or workload by name — not a generic capability claim
- Verifies `executability_hours` ≤ 8 for the recommended option, or surfaces an advisory if > 8
- Verifies each `relevance_explanation` is longer than 20 characters

**Activation triggers:** Requests to plan a POC, evaluate POC options, figure out what to demo, or decide what to build for the customer.

**Handoff / coordination:** On explore path: SA reviews options and selects one. On confirm path: 5 parallel artifact tools execute. Confirm path is a terminal action — no further POC strategy calls are needed.

---

### Handler: `PocStrategistHandler` (`agent/tools/specialists.py`)

**Explore path implementation:** PocStrategistHandler makes EXACTLY 3 parallel `asyncio.gather()` calls to the poc_strategist sub-agent — one call per angle (performance, migration, cost/efficiency). Each call receives the full `[CUSTOMER CONTEXT]` block plus exactly one angle. PocStrategistHandler does NOT make one call asking the sub-agent to return 3 options.

**Synthesis call:** After the 3 parallel calls complete, PocStrategistHandler makes one synthesis LLM call (not a Python f-string) to produce the `recommendation.rationale`. The synthesis call has access to all 3 option results and the customer context.

**Partial success policy:**
- 3 of 3 calls succeed: proceed with all 3 options
- 2 of 3 calls succeed: proceed with 2 options, include `failed_angles` field naming the failed angle
- 1 or 0 of 3 calls succeed: return `ToolResult(status="error", message="POC strategy generation failed — insufficient options")`

**Confirm path implementation:** PocStrategistHandler detects confirmation intent via string matching on the SA's message: "option 1," "option 2," "option 3," "go with," "use the," "let's do." On detection, returns `ToolResult(status="parallel", parallel_tools=["generate_diagram", "generate_bom", "generate_jep", "generate_terraform", "generate_presentation"])` without calling the sub-agent.

**Python validation (not LLM calls):**
- Assert `poc_options` is an array of length ≥ 2
- For each option: assert `relevance_score` is an integer 1–10, assert `relevance_explanation` length > 20, assert `executability_hours` is a positive integer, assert `oci_services` is a non-empty array
- Assert `recommendation.rationale` length > 50

**Constraints:** PocStrategistHandler MUST use `asyncio.gather()` for parallel calls — NOT a sequential loop. PocStrategistHandler MUST NOT make architecture decisions during synthesis.

---

### Sub-Agent: `poc_strategist` (`sub_agents/poc_strategist/`, port 8090, temperature 0.1 per call)

The poc_strategist sub-agent is called once per angle. It returns EXACTLY ONE POC option for the specified angle.

**Input contract:**
```json
{
  "task": "string — POC strategy request for ONE specific angle",
  "angle": "string — one of: performance, migration, cost_efficiency",
  "customer_context": "string — full [CUSTOMER CONTEXT] block"
}
```

**System prompt mandate:** The poc_strategist sub-agent is an OCI POC design specialist. It receives one angle and produces one highly specific POC option for that angle. The `wow_moment` field must reference the customer's specific platform, workload, or data by name. Generic wow moments are never acceptable. Temperature 0.1 per call enforces specificity and consistency in POC option design.

**Output contract:**
```json
{
  "poc_options": [
    {
      "option_name": "string",
      "angle": "string",
      "relevance_score": "integer 1-10",
      "relevance_explanation": "string — >20 characters",
      "executability_hours": "integer",
      "cost_effectiveness": "string",
      "security_highlights": ["string"],
      "oci_services": ["string"],
      "wow_moment": "string — references customer platform or workload by name",
      "demo_script_summary": "string"
    }
  ]
}
```

The `poc_options` array MUST contain exactly 1 element per call. The handler makes 3 calls to get 3 options.

**Must NEVER:**
- Return more than 1 option per call (handler makes 3 calls for 3 options)
- Use generic wow moments that do not reference the customer's specific context
- Call other sub-agents
- Ask clarifying questions
- Invent OCI service names

---

### Definition of Done

**What the SA sees (explore path):** A ranked list of 3 POC options with relevance scores, executability hours, wow moments, and a recommended option with rationale. The SA selects one and confirms to trigger the fan-out.

**What the SA sees (confirm path):** Acknowledgement that the POC plan is confirmed, followed by parallel generation of diagram, BOM, JEP, Terraform, and presentation artifacts. Each artifact completes and is delivered as it finishes.

**Technical verification:**
- Explore: `poc_options` length ≥ 2, each `wow_moment` contains the customer's platform name or workload term
- Explore: `recommendation.rationale` length > 50
- Confirm: `ToolResult(status="parallel", parallel_tools=[5 tool names])` returned — no sub-agent called
- Handler uses `asyncio.gather()` for parallel sub-agent calls (verifiable in handler code)
- Critic schema passes

---

## 3.10 Presentation — `generate_presentation`

### Hat: `oci_presentation_writer` (`agent/hats/oci_presentation_writer.md`)

**Identity:** When wearing this hat, Archie IS an Oracle SE who will hand this deck to the customer at the POC kickoff meeting. Archie opens the file, flips through all 7 slides, and asks: "Would I be embarrassed to give this to the customer?" Archie approves the deck when the answer is "no" — when Archie would confidently email it right now.

**BOUNDARY:** This tool ONLY activates when `poc_recommendation` exists in context, meaning the SA has confirmed a POC option from the explore path. For pre-POC narrative decks used in early customer conversations, Archie routes to `generate_sales_deck` instead.

**NOTE:** The sub-agent is a deterministic renderer that calls `render_oci_powerpoint.py` internally — it is not an LLM. Archie's pre-action and post-action reviews evaluate the input spec and the output artifact; they do not influence the rendering logic. Archie does not try to "prompt" the renderer — Archie validates before and after.

**Pre-action reasoning:** Before calling the PresentationHandler, Archie:
- Verifies `poc_recommendation` and `customer_name` are present in context. If either is absent, Archie does not call the handler — routes the SA back to the POC confirm path first.
- Checks BOM completion status: if the BOM artifact is in-progress or absent, marks slide 5 in the render spec as "Pending" rather than failing
- Checks JEP completion status: if the JEP is in-progress or absent, marks slide 6 in the render spec as "Pending" rather than failing

**Post-action evaluation:** After receiving the presentation output, Archie:
- Verifies exactly 7 slides are present
- Verifies the artifact key ends in `.pptx`
- Verifies Oracle branding: Oracle Red `#C74634` or sourced from Oracle OCI Architecture Toolkit PPTX master

**Coordination:** In the POC fan-out workflow, `generate_presentation` runs in parallel with `generate_diagram`, `generate_bom`, `generate_jep`, and `generate_terraform`. If BOM or JEP complete after presentation starts, the presentation slides 5 and 6 will read "Pending" until re-generated.

**Activation triggers:** ONLY after `poc_recommendation` is confirmed in context. Does not activate from any other path.

---

### Handler: `PresentationHandler` (`agent/tools/presentation.py`)

**Pre-call validation (error if absent):**
- Assert `poc_recommendation` is present in context — if absent, return `ToolResult(status="error", message="generate_presentation requires a confirmed POC recommendation. Use generate_poc_plan to select a POC option first.")`
- Assert `customer_name` is present in context — if absent, return `ToolResult(status="error", message="customer_name is required for presentation generation.")`

**Context injection:** PresentationHandler MUST pass a structured render spec (not a free-form task string) to the sub-agent. The render spec contains:
- `customer_name`, `poc_recommendation_summary`
- BOM status: `monthly_total` if available, else `"Pending"`
- JEP status: `week_numbers` and `phase_titles` if available, else `"Pending"`
- Diagram artifact key if available

**Python validation (not LLM calls):**
- Assert the returned bytes are non-empty
- Assert the returned bytes constitute a valid ZIP archive: `zipfile.is_zipfile(BytesIO(response_bytes))` must return `True` (`.pptx` files are ZIP archives)
- If bytes are empty or ZIP check fails: retry exactly once. On second failure: return `ToolResult(status="error", message="Presentation renderer returned invalid output after retry.")`

**Artifact persistence:** Save the validated bytes with key `presentation/{customer_id}/v{N}.pptx` where N is the next available version. The key MUST end in `.pptx`.

**Download handler:** When the SA downloads this artifact, the response `Content-Type` header MUST be `application/vnd.openxmlformats-officedocument.presentationml.presentation`.

**Constraints:** PresentationHandler MUST NOT pass a free-form task string to the renderer. PresentationHandler MUST NOT accept empty bytes or an invalid ZIP. PresentationHandler MUST retry exactly once on failure — not zero times, not more than once.

---

### Sub-Agent: `presentation` (`sub_agents/presentation/`, port 8091, temperature N/A — deterministic renderer)

The presentation sub-agent is a deterministic renderer. It calls `render_oci_powerpoint.py` internally and is not an LLM. It has no temperature setting. It does not reason. It renders.

**7-slide structure (invariant):**
1. Title slide — customer name, POC name, Oracle logo, Oracle Red background
2. Customer Challenge — the specific pain statement from `poc_recommendation`
3. OCI Architecture — diagram thumbnail or placeholder if diagram artifact key is provided
4. Key OCI Services — service icons and one-line descriptions from `poc_recommendation.oci_services_required`
5. Cost Estimate — BOM monthly_total, or "BOM Pending — Estimated Cost TBD" if BOM status is Pending
6. Implementation Plan — JEP phase summary with week numbers, or "Implementation Plan Pending — JEP in Progress." if JEP status is Pending
7. Next Steps — three action items based on the POC plan

`render_oci_powerpoint.py` raises `ValueError` if slide count ≠ 7. This exception propagates as a handler error.

Oracle branding: Oracle Red `#C74634` for title slide background and accent elements. Service icons sourced from Oracle OCI Architecture Toolkit PPTX stencil.

**Input contract:**
```json
{
  "render_spec": {
    "customer_name": "string",
    "poc_recommendation_summary": "string",
    "bom_status": "string — monthly_total or 'Pending'",
    "jep_status": "string — phase summary or 'Pending'",
    "diagram_artifact_key": "string or null"
  }
}
```

**Output contract:**
```json
{
  "artifact_bytes_base64": "string — base64-encoded .pptx bytes",
  "artifact_key": "string — ending in .pptx",
  "slide_count": 7
}
```

**Must NEVER:**
- Return a slide count other than 7
- Return empty bytes
- Return an invalid ZIP archive
- Ask clarifying questions
- Use "TBD" in slide 5 when BOM monthly_total was provided
- Use Oracle branding colors other than `#C74634` for primary accent elements

---

### Definition of Done

**What the SA sees:** A `.pptx` file available for download. The SA opens it and sees a 7-slide Oracle-branded deck with the customer's name, POC recommendation, OCI architecture, cost estimate (or pending notice), and implementation timeline. The SA can email this to the customer's project lead or present it at the POC kickoff meeting.

**Technical verification:**
- `artifact_key` ends in `.pptx`
- Returned bytes pass `zipfile.is_zipfile()` check (handler Python code)
- `slide_count` equals 7
- Download response `Content-Type` is `application/vnd.openxmlformats-officedocument.presentationml.presentation`
- File is saved in `document_store` at key `presentation/{customer_id}/v{N}.pptx`
- Critic schema passes

---

# Appendix A: Forge Mechanism Reference

This appendix is a quick reference for implementation teams. It does not add to the specification — it clarifies which existing Forge mechanisms satisfy which Archie requirements.

| Archie Requirement | Forge Mechanism | How to Use |
|--------------------|-----------------|------------|
| Auto-activate a hat before a tool call | `requires_hat` on `ToolSpec` | Set `requires_hat="hat_name"` in `build_forge()` |
| Run multiple tools in parallel | `ToolResult(status="parallel", parallel_tools=[...])` | Return from handler on POC confirm path |
| Retry a tool after expert rejection | `ToolResult(status="iterate")` | Return from handler when Python validation fails |
| Max 3 correction attempts | Built into Forge's correction loop | No handler code needed — Forge manages the count |
| Expert pre-action LLM call | Forge's expert pre-action phase | Invoked automatically when `requires_hat` is set |
| Expert post-action LLM call | Forge's expert post-review phase | Invoked automatically when `requires_hat` is set |
| Escalate to SA on 3rd failure | Forge's escalation on max iterations | Forge surfaces to the user when iterate count exceeds max |

---

# Appendix B: Sub-Agent Port Map

| Sub-Agent | Port | Temperature |
|-----------|------|-------------|
| diagram | 8082 | 0.0 |
| bom | 8083 | 0.2 |
| pov | 8084 | 0.7 |
| jep | 8085 | 0.7 |
| waf | 8086 | 0.5 |
| tech_research | 8088 | 0.5 |
| terraform | 8087 | 0.2 |
| sales_deck | 8089 | 0.6 |
| poc_strategist | 8090 | 0.1 per call |
| presentation | 8091 | N/A (deterministic renderer) |

---

# Appendix C: Context Block Conventions

Handlers use structured blocks injected into the task payload. These conventions are consistent across all handlers.

**`[CONFIRMED CONTEXT]` block:** Contains all customer and engagement facts that are known at the time of handler invocation. Every handler MUST inject this block. The block prevents sub-agents from asking for information Archie already has.

**`[SUB-AGENT INSTRUCTIONS]` block:** Used by the tech research hat to encode the classified workload pattern, applied defaults, and specific question being asked. The handler passes this verbatim to the sub-agent.

**`[DECK BRIEF]` block:** Used by the sales deck hat to encode the available artifacts (POV, BOM, diagram) and their key facts. The handler fetches artifact contents and injects summaries.

**`[CUSTOMER CONTEXT]` block:** Used by the POC strategist handler for each of the 3 parallel sub-agent calls. Contains the full customer pain statement, platform, and constraints.

**`[AI/ML SERVICES REQUIRED]` block:** Emitted by the diagram hat pre-action when AI/ML keywords are detected. The handler passes this to the diagram sub-agent as part of the task description, not as a separate field.

All block names are uppercase, bracket-delimited, and consistent across handlers and sub-agents. If a block name changes, both the handler and the sub-agent system prompt must be updated together.

---

# Appendix D: Output Key Conventions

| Tool | Key Field | Format | Example |
|------|-----------|--------|---------|
| generate_diagram | `artifact_key` | `diagram/{customer_id}/v{N}.drawio` | `diagram/acme/v3.drawio` |
| generate_bom | `artifact_key` | `bom/{customer_id}/v{N}.xlsx` | `bom/acme/v1.xlsx` |
| generate_waf | `artifact_key` | `waf/{customer_id}/v{N}.json` | `waf/acme/v1.json` |
| generate_terraform | `artifact_key` | `terraform/{customer_id}/v{N}.zip` | `terraform/acme/v2.zip` |
| generate_pov | `artifact_key` | `pov/{customer_id}/v{N}.md` | `pov/acme/v1.md` |
| generate_jep | `doc_key` | `jep/{customer_id}/v{N}` | `jep/acme/v1` |
| generate_tech_research | `artifact_key` | `research/{customer_id}/v{N}.json` | `research/acme/v1.json` |
| generate_sales_deck | `artifact_key` | `deck/{customer_id}/v{N}.json` | `deck/acme/v1.json` |
| generate_poc_plan | `artifact_key` | `poc/{customer_id}/v{N}.json` | `poc/acme/v1.json` |
| generate_presentation | `artifact_key` | `presentation/{customer_id}/v{N}.pptx` | `presentation/acme/v1.pptx` |

The JEP tool is the only tool that uses `doc_key` instead of `artifact_key`. This distinction is enforced by the JEP handler in Python code and by the Critic's schema check.

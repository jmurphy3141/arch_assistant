# Current State — Sub-Agents

This document describes the current state of every Archie sub-agent: what it does,
how it is called, what it expects, and what it produces. Written 2026-05-28 as a
baseline for reviewing and strengthening each agent.

Sub-agents are independent A2A HTTP services. Archie's Forge orchestrator calls them
through `agent/sub_agent_client.py` → `call_sub_agent(name, task, engagement_context, trace_id)`.
Each runs on a dedicated port, has its own system prompt, and returns an `A2AResponse`
with `status`, `result`, and `trace` fields.

---

## Diagram

**Intent:** Turns a natural-language workload description into a valid draw.io XML
architecture diagram using OCI-standard icons and layout conventions. It is the
highest-frequency sub-agent and the anchor for all downstream artifacts.

| Field | Value |
|---|---|
| Port | 8082 |
| Temperature | 0.0 (deterministic) |
| Max Tokens | 4000 |
| Handler | `agent/tools/diagram.py` — `DiagramHandler` |
| Output format | draw.io XML string |

### Inputs

- **Required:** `task` — workload description or architecture request
- **Optional:** `diagram_name`, `customer_id`, `engagement_context`, `trace_id`

### System Prompt

The LLM is instructed to produce a machine-readable JSON layout specification (not XML
directly). Key rules:

- Internet users sit outside the region boundary; services sit inside VCN/subnet containers
- AI/RAG patterns have explicit shapes: GenAI node, OpenSearch as vector store, Data
  Science for ML workloads, app tier orchestrates retrieval
- Use exact `oci_type` values from a built-in service mapping table (e.g.,
  `generative_ai`, `opensearch`, `analytics_cloud`, `data_science`)
- If blocking facts are missing, return `status: "need_clarification"` with a `questions`
  array instead of guessing

### Output Contract

On success: JSON layout spec consumed by the Python pipeline:
```
intent_compiler → layout_engine → drawio_generator → draw.io XML
```

On clarification needed:
```json
{ "status": "need_clarification", "questions": ["..."] }
```

### Handler Notes

`DiagramHandler.__call__()` calls `_call_generate_diagram()`, which:
1. Checks context sufficiency
2. Applies expert review corrections if `_forge_correction` is present
3. Calls the diagram sub-agent A2A
4. Validates and compiles the layout intent
5. Generates draw.io XML via `generate_drawio()`
6. Persists the artifact; returns `ToolResult` with `drawio_xml`, node/edge counts

### Current State Notes

- Temperature 0.0 is correct for deterministic layout but may suppress creative
  topology solutions for unusual architectures
- System prompt covers common AI patterns but does not include OCI Data Flow, OCI
  Streaming, or newer GenAI service shapes explicitly
- No revision/diff support — every call generates from scratch

---

## BOM

**Intent:** Produces a priced Oracle Cloud Infrastructure Bill of Materials from
workload sizing inputs, using specific OCI SKU codes and the authoritative pricing
cache. It is the cost anchor for every engagement.

| Field | Value |
|---|---|
| Port | 8083 |
| Temperature | 0.2 (low — deterministic pricing) |
| Max Tokens | 4000 |
| Handler | `agent/tools/bom.py` — `BomHandler` |
| Output format | JSON with `bom_payload` |

### Inputs

- **Required:** `task` — workload sizing request
- **Optional:** `region`, `engagement_context` (with Archie Canonical Memory block), `trace_id`

### System Prompt

The LLM is given explicit OCI SKU codes and must produce line items with those exact
codes. Key rules:

- **Default shape:** VM.Standard.E5.Flex (SKU B97384 for OCPU, B97385 for memory)
- Other shapes: E4.Flex (B93113/B93114), E6.Flex (B111129/B111130), X9 (B94176/B94177),
  A1.Flex (B93297/B93298), BM.GPU4.8
- Archie Canonical Memory block is authoritative — preserve prior BOM payload except
  for explicitly superseded line items
- Return JSON with `type: "final"` and `bom_payload` on success; `type: "needs_input"`
  with `reply` when clarification is needed
- Never wrap JSON in markdown fences

### Output Contract

```json
{
  "type": "final",
  "bom_payload": {
    "line_items": [
      {
        "sku": "B97384",
        "description": "VM.Standard.E5.Flex — OCPU",
        "quantity": 4,
        "unit": "OCPU",
        "unit_price": 0.03,
        "monthly_cost": 87.6,
        "instance_count": 3
      }
    ],
    "totals": { "estimated_monthly_cost": 1234.56 }
  }
}
```

### Handler Notes

`BomHandler.__call__()`:
1. Prepares and hydrates BOM tool args from context and memory
2. Calls the BOM sub-agent via `sub_agent_client.call_sub_agent("bom", ...)`
3. Parses the JSON response, extracts `bom_payload`
4. Optionally enriches payload (appends missing WAF/database line items if mentioned)
5. Returns `ToolResult` with `bom_payload` data, service count, and monthly total summary

### Current State Notes

- SKU list in the system prompt is static; new OCI shapes (E6.Flex is present, but
  any newer shape releases require a manual update)
- No explicit validation of pricing accuracy — the model is trusted to apply the
  embedded prices correctly
- `bom_stub.py` exists for offline/test use but the live sub-agent is the only
  production path

---

## POV

**Intent:** Writes a polished, customer-facing Point-of-View document that connects
the customer's business challenge to OCI capabilities, migration narrative, and
expected outcomes. It is the primary narrative deliverable for an early-stage engagement.

| Field | Value |
|---|---|
| Port | 8084 |
| Temperature | 0.7 (narrative creativity) |
| Max Tokens | 4000 |
| Handler | `agent/tools/specialists.py` — `PovHandler` |
| Output format | Markdown |

### Inputs

- **Required:** `task` — POV brief or request
- **Optional:** `customer_name`, `engagement_context`, `prior_version` (for revision), `trace_id`

### System Prompt

The LLM is instructed to write an executive-voice document with technical depth.
Required content: customer situation and challenge, OCI architecture direction with
service rationale, migration/implementation narrative with realistic assumptions,
expected business impact, success metrics, risks, next steps. Key rules:

- Use customer-specific facts; frame assumptions explicitly if data is missing
- Do not invent unsupported metrics or SLAs
- When revising a prior draft, preserve useful content and apply requested feedback directly
- Output clear Markdown sectioning suitable for a light editorial pass

### Output Contract

Markdown document. No structured JSON schema — the entire response is the document.

### Handler Notes

`_SpecialistHandler.__call__()` (as `PovHandler`):
1. Checks POV context sufficiency via `archie_memory._pov_has_sufficient_context()`
2. If insufficient, returns `needs_input` with targeted questions before calling
3. Hydrates args from context and enforces memory contract
4. Applies `_forge_correction` if present
5. Calls `sub_agent_client.call_sub_agent("pov", ...)` with engagement context
6. Saves result via `document_store.save_doc()`
7. Returns `ToolResult` with version and artifact key

### Current State Notes

- Context sufficiency check may block the tool prematurely when conversational context
  is rich but not yet stored in structured memory fields
- No explicit word count or section count target in the system prompt
- Revision flow works but prior_version must be passed explicitly; no auto-retrieval
  of the latest POV from storage in the sub-agent itself

---

## JEP

**Intent:** Produces an implementation-grade Joint Engagement Plan executable by both
Oracle and customer teams, covering scope, workstreams, milestones, resources,
dependencies, and success gates. It is the delivery artifact that bridges POV approval
to POC execution.

| Field | Value |
|---|---|
| Port | 8085 |
| Temperature | 0.7 (structured creativity) |
| Max Tokens | 4000 |
| Handler | `agent/tools/specialists.py` — `JepHandler` |
| Output format | Markdown |

### Inputs

- **Required:** `task` — JEP brief or request
- **Optional:** `customer_name`, `engagement_context`, `prior_version`, `feedback`, `trace_id`

### System Prompt

The LLM produces an implementation-grade plan document. Required sections: engagement
objective and success criteria, in-scope/out-of-scope, workstreams, milestones,
timeline, owners, dependencies, OCI environment prerequisites and validation steps,
risks, mitigations, decision gates, deliverables, next actions. Key rules:

- Use Markdown with clear headings and tables where useful
- When feedback or prior_version is provided, treat as a revision — update without
  losing approved scope or decisions

### Output Contract

Markdown document. No structured JSON schema.

### Handler Notes

`_SpecialistHandler.__call__()` (as `JepHandler`):
1. **First:** checks JEP lifecycle lock via `jep_lifecycle.generate_policy_block_payload()`.
   If an approved JEP exists, returns `blocked` with `jep_state` — the sub-agent is
   never called
2. Hydrates args, enforces memory contract
3. Applies corrections if present
4. Calls sub-agent, saves document
5. Calls `jep_lifecycle.mark_generated()` after save
6. Returns `ToolResult` with `jep_state` and `lock_outcome: "allowed"`

### Current State Notes

- Lifecycle lock is enforced by the handler, not the sub-agent — the sub-agent itself
  has no awareness of the lock state
- Revision flow requires the user to explicitly request a revision before the lock
  allows regeneration
- No explicit kickoff Q&A flow in the system prompt; the hat's Pre-Action checklist
  handles question surfacing at the Archie level

---

## WAF

**Intent:** Reviews an OCI architecture against the six Oracle Well-Architected
Framework pillars and produces a prioritised findings report. It is the quality gate
between diagram approval and Terraform generation.

| Field | Value |
|---|---|
| Port | 8086 |
| Temperature | 0.5 (balanced) |
| Max Tokens | 4000 |
| Handler | `agent/tools/specialists.py` — `WafHandler` |
| Output format | Markdown |

### Inputs

- **Required:** `task` — review request
- **Optional:** `architecture_summary`, `diagram_context`, `engagement_context`, `feedback`, `trace_id`

### System Prompt

Review the architecture against six pillars: operational excellence, security,
reliability, performance efficiency, cost optimization, sustainability. Must include:
executive posture summary, pillar-by-pillar observations with gaps, prioritised OCI-
specific remediation actions. OCI controls to reference: IAM, compartments, KMS, WAF,
NSGs, private subnets, logging, monitoring, backup, DR, tagging, budget controls.

### Output Contract

Markdown document with sections for each pillar. Handler post-processes to ensure all
required headings are present via `_ensure_waf_markdown_sections()`.

### Handler Notes

`_SpecialistHandler.__call__()` (as `WafHandler`):
1. Calls sub-agent with architecture context
2. Calls `_ensure_waf_markdown_sections()` to add any missing pillar headings
3. Attempts to parse JSON for P1 findings count (if LLM returns structured data)
4. Saves document; returns `ToolResult` with findings summary `"N findings (M P1)."`

### Current State Notes

- System prompt does not enforce a specific finding severity schema (P1/P2/P3) — the
  handler tries to parse it but the LLM is not required to produce it
- No explicit P1 definition or severity rubric in the system prompt
- `architecture_summary` and `diagram_context` are optional — WAF can run without
  the actual diagram, which weakens finding quality
- Six-pillar coverage is correct but sustainability pillar guidance is minimal

---

## Terraform

**Intent:** Generates production-ready OCI Terraform modules (provider v5+) covering
the confirmed architecture, including variables, outputs, and a README. It is the IaC
deliverable that makes the architecture deployable.

| Field | Value |
|---|---|
| Port | 8087 ⚠️ PORT CONFLICT with tech_research |
| Temperature | 0.2 (low — code quality) |
| Max Tokens | 6000 |
| Handler | `agent/tools/terraform.py` — `TerraformHandler` |
| Output format | JSON with four file keys |

### Inputs

- **Required:** `task` — architecture description and Terraform scope
- **Optional:** `architecture_summary`, `region`, `compartment_id`, `engagement_context`, `trace_id`

### System Prompt

Generate exactly four files: `main.tf`, `variables.tf`, `outputs.tf`, `README.md`.
Key rules:
- OCI Terraform provider v5+ with explicit `required_providers`
- Variables for region and compartment_ocid
- Private-by-default networking — no public IPs unless explicitly required
- NSGs and security lists for all resources
- Useful outputs (endpoint URLs, OCIDs, connection strings)
- Return JSON object with keys `main_tf`, `variables_tf`, `outputs_tf`, `readme_md`
- No markdown fences around the JSON

### Output Contract

```json
{
  "main_tf": "terraform { ... }",
  "variables_tf": "variable \"region\" { ... }",
  "outputs_tf": "output \"db_endpoint\" { ... }",
  "readme_md": "# OCI Terraform ..."
}
```

### Handler Notes

`TerraformHandler.__call__()`:
1. Checks context for architecture definition and scope
2. Calls sub-agent; parses JSON result with `_parse_files()` (handles both JSON and
   text-block fallback)
3. Maps JSON keys to file names (`main_tf` → `main.tf`, etc.)
4. Saves as a versioned Terraform bundle via `document_store`
5. Returns `ToolResult` with file list, version, artifact key

### Current State Notes

- **Port 8087 conflicts with tech_research** — only one can run at a time; needs resolution
- Config comment says "set to code-optimised model OCID when available" — no OCI code
  model is currently configured; falls back to the default general-purpose model
- No explicit Terraform validation or `terraform validate` step — correctness depends
  entirely on LLM output quality
- No module registry references; all resources are inline

---

## Tech Research

**Intent:** Evaluates two or more OCI architecture options for a given workload and
produces a structured assessment with service recommendations, sizing hints, and a risk
register. It is the pre-BOM research step for ambiguous or complex engagements.

| Field | Value |
|---|---|
| Port | 8087 ⚠️ PORT CONFLICT with terraform |
| Temperature | 0.5 (research balance) |
| Max Tokens | 6000 |
| Handler | `agent/tools/specialists.py` — `TechResearchHandler` |
| Output format | JSON with `research_payload` |

### Inputs

- **Required:** `task` — technology research or comparison request
- **Optional:** `customer_name`, `engagement_context`, `trace_id`

### System Prompt

Evaluate OCI infrastructure options for the described workload. Key rules:
- Name the workload pattern first (3-tier, microservices, ML, data platform, batch, RAG, etc.)
- Evaluate at least 2 concrete OCI options per question — never single-path
- Use exact OCI service names and shapes (e.g., `VM.Standard.E5.Flex` not "compute instance")
- Include rough monthly estimates (order of magnitude) per option
- Surface at least 3 risks with severity (High/Medium/Low) and OCI-specific mitigation
- Return JSON with `type: "final"` and `research_payload`, or `type: "needs_input"`

### Output Contract

```json
{
  "type": "final",
  "research_payload": {
    "workload_pattern": "...",
    "options_evaluated": [
      {
        "option_name": "...",
        "oci_services": ["..."],
        "pros": ["..."],
        "cons": ["..."],
        "sizing_hints": { ... },
        "estimated_monthly_cost": "~$X–Y"
      }
    ],
    "recommendation": { "option": "...", "rationale": "..." },
    "risk_register": [{ "risk": "...", "severity": "High", "mitigation": "..." }]
  }
}
```

### Handler Notes

`_SpecialistHandler.__call__()` (as `TechResearchHandler`):
1. Hydrates args from context and enforces memory contract
2. Calls sub-agent, saves result as doc type "research"
3. Returns `ToolResult` with version and artifact key

### Current State Notes

- **Port 8087 conflicts with terraform** — this is a blocking deployment issue
- The `infra_tech_research` hat has 10 activation triggers (more than any other hat)
  but the sub-agent system prompt has limited guidance on what qualifies as sufficient
  research depth
- No explicit `sizing_hints` schema in the system prompt — the field name is expected
  by the BOM handler but the LLM is not required to produce it in a specific format
- `TechResearchHandler` is a thin wrapper; no post-processing or validation of the
  research payload structure

---

## Sales Deck

**Intent:** Generates a structured JSON slide specification for a customer-facing OCI
sales presentation, following an 8-slide standard structure with customer-specific
content. It is the pre-POC commercial narrative tool.

| Field | Value |
|---|---|
| Port | 8088 |
| Temperature | 0.6 (moderate creative) |
| Max Tokens | 8000 |
| Handler | `agent/tools/specialists.py` — `SalesDeckHandler` |
| Output format | JSON (`deck_payload` with `slides` array) |

### Inputs

- **Required:** `task` — deck request
- **Optional:** `customer_context`, `existing_artifacts`, `deck_type`, `engagement_context`, `trace_id`

### System Prompt

Produce structured JSON slide specs for a customer-facing OCI sales deck. Standard
structure: 8 slides — Title, Challenge, Solution Overview, Architecture, Bill of
Materials, Why OCI, Next Steps, Appendix. Key rules:
- Every slide title is a complete declarative sentence
- One primary message per slide; presenter notes on every slide
- Use customer-specific facts — never invent SLAs, benchmarks, or guarantees
- Cost figures come from BOM artifact or framed as "$X–Y/month (to be confirmed)"
- "Why OCI" slide names OCI-specific advantages (Exadata, dedicated region, DB
  co-location, pricing model)
- Return JSON with `type: "final"` and `deck_payload` containing `slides` array

### Output Contract

```json
{
  "type": "final",
  "deck_payload": {
    "slides": [
      {
        "slide_number": 1,
        "title": "...",
        "content": ["..."],
        "presenter_notes": "..."
      }
    ]
  }
}
```

### Handler Notes

`_SpecialistHandler.__call__()` (as `SalesDeckHandler`):
1. Hydrates args, enforces memory contract, applies corrections
2. Calls sub-agent
3. **Saves as JSON** via `_save_json_doc()` — unlike other specialist handlers which
   save Markdown, the sales deck is stored as pretty-printed JSON
4. Returns `ToolResult` with version and artifact key

### Current State Notes

- The deck output is JSON, not a renderable file — there is no PPTX rendering path
  for the sales deck (only for `generate_presentation`). This is a separate tool from
  the POC presentation
- No UI download support specifically for JSON deck files
- The "Why OCI" slide rule names specific Oracle advantages but the system prompt
  does not provide guardrails against hallucinating product details (e.g., specific
  benchmark numbers)
- Relationship to `generate_presentation` (the POC PowerPoint tool) is unclear in
  the user-facing workflow — two separate presentation tools exist

---

## POC Strategist

**Intent:** Explores one scored OCI POC option for a single customer engagement angle
(migration/modernization, performance/scale/AI, or cost/TCO). Three instances run in
parallel via `PocStrategistHandler` to produce three distinct ranked options from a
single `generate_poc_plan` call.

| Field | Value |
|---|---|
| Port | 8090 |
| Temperature | 0.1 (very low — deterministic JSON) |
| Max Tokens | 2048 |
| Handler | `agent/tools/specialists.py` — `PocStrategistHandler` |
| Output format | JSON (one POC option) |

### Inputs

- **Required:** `task` — customer brief and pain statement
- **Optional:** `angle` (in engagement_context), `customer_context`, `engagement_context`, `trace_id`

### System Prompt

Generate exactly one POC option for the given angle. The option must prove the
customer's stated pain in something an OCI SE can build and demo in under 8 hours.
Key rules:
- `CRITICAL OUTPUT FORMAT` header at the top: return only raw JSON, no markdown
- Scoring: `relevance_score` (1–10), `executability_hours` (integer, prefer ≤8)
- Option names must include the customer workload, platform, or pain
- Security highlights must name OCI controls (IAM, Vault, WAF, Cloud Guard, etc.)
- Return only the single JSON object — no explanation, no list

### Output Contract

```json
{
  "option_name": "Live Oracle DB migration to ADB-Dedicated",
  "relevance_score": 9,
  "executability_hours": 6,
  "cost_effectiveness": "Removes manual DBA effort and reduces overprovisioned compute.",
  "security_highlights": ["OCI Vault", "private endpoint", "Cloud Guard"],
  "wow_moment": "Cut over sample workload with near-zero downtime.",
  "demo_script_summary": "Show source, run migration, validate app, compare ops.",
  "oci_services": ["Oracle Autonomous Database Dedicated", "OCI Database Migration"]
}
```

### Handler Notes

`PocStrategistHandler.__call__()`:
1. Checks for `action="confirm"` or `confirmed_option_name` → routes to fan-out
2. Falls back to legacy `_user_message` confirmation detection if not an explicit call
3. For exploration: fires 3 parallel `asyncio.gather()` calls to the sub-agent, one
   per angle
4. Collects results; skips failed angles gracefully (returns `blocked` only if all 3 fail)
5. Ranks options by `relevance_score / max(executability_hours, 1)`
6. Saves result as JSON via `_save_json_doc()`; returns `ToolResult` with ranked options
   and recommendation

`_extract_json()` is applied in `server.py` after LLM inference to recover JSON from
markdown-wrapped responses (added in p56f).

### Current State Notes

- The sub-agent runs three times per `generate_poc_plan` call — if the LLM returns
  markdown despite the critical format instruction, `_extract_json()` recovers the
  JSON but the fallback is not guaranteed for all response shapes
- The recommendation rationale is auto-generated by the handler (not the LLM) and is
  formulaic: "highest relevance (N/10) with Nh build time" — not customer-specific
- No memory of previously explored options across sessions unless poc_options is stored
  in decision_context

---

## Presentation

**Intent:** Generates a 7-slide Oracle-standard client-facing PowerPoint POC deck using
the Oracle OCI Architecture Toolkit v24.1 for icon stencils. It is the final deliverable
in the POC fan-out and the artifact handed to the customer after POC confirmation.

| Field | Value |
|---|---|
| Port | 8091 |
| Temperature | 0.2 |
| Max Tokens | 1024 |
| Handler | `agent/tools/presentation.py` — `PresentationHandler` |
| Output format | base64-encoded PPTX bytes |

### Inputs

- **Required:** `task`, `customer_name`
- **Optional:** `poc_name`, `oci_services`, `bom_summary`, `jep_phases`, `engagement_context`, `trace_id`

### System Prompt

The sub-agent's system prompt describes the JSON spec structure for a 7-slide deck.
However, **the current implementation does not call an LLM** — `server.py` reads the
spec directly from `engagement_context` and calls `render_oci_powerpoint.render()`
immediately. The system prompt exists but is not used in the normal code path.

### Output Contract

The `handle()` function returns base64-encoded raw PPTX bytes. The handler decodes
them and saves to object storage as `presentation/{customer_id}/vN.pptx`.

### Handler Notes

`PresentationHandler.__call__()`:
1. Reads `poc_option` from args or `poc_recommendation` from memory
2. Requires `customer_name` — returns `needs_input` if absent
3. Builds `task_payload` with poc_name, customer_name, pain_statement, oci_services,
   bom_summary, bom_rows, jep_phases
4. Calls sub-agent via `call_sub_agent("presentation", ...)`
5. Decodes base64 PPTX bytes; saves via `_save_pptx_doc()` with versioning
6. Returns `ToolResult` with artifact key

`_save_pptx_doc()` writes two paths (customer-first + legacy), a LATEST.pptx, and a
MANIFEST.json — consistent with the storage pattern used by all other document types.

### Current State Notes

- The system prompt is not used — the server renders directly from the spec. The
  system_prompt.md describes a JSON spec generation step that does not happen
- `render_oci_powerpoint.render()` uses the Oracle OCI Architecture Toolkit v24.1
  (48 slides, 915 named shapes) for icon stencils — the official toolkit was committed
  in PR #242
- The `jep_phases` and `bom_rows` fields are passed through but the render script
  may not fully populate all slides if those fields are empty (slides are generated
  with placeholder content)
- No LLM-driven content generation means the deck content quality depends entirely
  on what is already in memory/decision_context — weak context produces thin slides

# Archie Implementation Phases

This document defines the two implementation phases for bringing Archie to the standard
set in `archie-golden-spec.md`. Each phase has a defined scope, observable gate criteria,
and a single human gate before Phase 2 begins: an internal Oracle SE trial.

---

## Phase 1 — Correctness + Conversational Foundation

**Goal:** Fix the correctness gaps in existing capabilities and replace the form-based UI
with a single conversational interface. No new capabilities. When Phase 1 is complete,
every existing tool works as specified in the golden spec and an SE can run a full
customer engagement from a single chat window.

**Gate:** Internal Oracle SE trial. The SE uses Archie on a real account. Phase 2 begins
when the SE says "I would use this on my next customer call" and none of the Phase 1
failure criteria fired.

---

### Backend: Correctness Changes

These are all changes to `agent/hats/`, `agent/tools/`, and `sub_agents/config.yaml`.
None touch `skillforge/forge.py`.

| Change | File | What changes |
|---|---|---|
| WAF: 6 pillars (not 5) | `agent/hats/oci_waf_reviewer.md` | Post-action mandatory check updated; Continuous Improvement pillar added to schema |
| WAF: 6-pillar validation in code | `agent/tools/specialists.py` (WafHandler) | Python validates 6 keys in `pillars` object before accepting result |
| Terraform: canonical file list | `agent/hats/terraform_for_oci.md` | Remove `provider.tf`; add `README.md` throughout hat (quality bar + post-action) |
| Terraform: no-OCID grep in handler | `agent/tools/terraform.py` (TerraformHandler) | grep for `ocid1.*` in .tf files in code before accepting bundle |
| JEP: `artifact_key` → `doc_key` | `agent/hats/jep_writer.md` | Post-action mandatory check corrected; consistent with sub-agent output contract |
| tech_research port: 8087 → 8086 | `sub_agents/tech_research/config.yaml` | Eliminates conflict with Terraform sub-agent |
| BOM: inject `[CONFIRMED CONTEXT]` | `agent/tools/bom.py` (BomHandler) | Handler assembles and injects confirmed sizing fields before every sub-agent call |
| BOM: arithmetic verification in code | `agent/tools/bom.py` (BomHandler) | Python computes `sum(qty × unit_price × 730)`, compares to `monthly_total` ± 0.5% |
| Diagram: single-question rule | `agent/hats/diagram_for_oci.md` | Pre-action instructions enforce one clarifying question maximum |

---

### Frontend: Conversational + Memory

**Remove:** The standalone form UIs. These are form-based access points to individual
tools and contradict the conversational second-brain model. After Phase 1 the chat
interface is the only interaction mode.

| Component | Action |
|---|---|
| `GenerateForm.tsx` | Remove |
| `TerraformForm.tsx` | Remove |
| `WafForm.tsx` | Remove |
| `JepForm.tsx` | Remove |
| `PovForm.tsx` | Remove |
| `BomAdvisor.tsx` | Remove (BOM generation flows from chat; XLSX export moves to ArtifactPreviewPanel) |

**Add: Engagement Memory Panel**

A persistent sidebar panel next to the chat showing what Archie currently knows about
this customer. Sourced from `GET /api/context/{customer_id}` (existing endpoint via
`context_store.py`). This makes the "second brain" tangible — the SE can see at a glance
that Archie has remembered the customer's challenge, the services discussed, and the
artifacts already generated.

```
┌─────────────────────────────────┐
│  ACME Financial Services        │
│  ─────────────────────────────  │
│  Challenge:                     │
│  $2M Oracle RAC licensing cost  │
│                                 │
│  Services in scope:             │
│  • Autonomous Database          │
│  • OKE                          │
│  • OCI Load Balancer            │
│                                 │
│  Artifacts:                     │
│  • diagram/acme/v2.drawio  ↗    │
│  • bom/acme/v1.xlsx        ↗    │
│  • waf/acme/v1.md          ↗    │
└─────────────────────────────────┘
```

Panel fields (from `context_store`):
- `customer_name`
- `customer_challenge` (first 80 characters)
- `oci_services_in_scope` (bullet list)
- `artifacts` (keys with clickable links to `/download?key=...`)

The panel updates live as Archie generates new artifacts during the conversation.

**Update: `ChatInterface.tsx`**

- The input placeholder changes to reflect the conversational model: "Describe your
  customer or ask Archie for an artifact..." rather than a generic text prompt.
- Artifact links inline in the chat (already partially present via `ArtifactPreviewPanel`)
  are promoted to first-class elements: diagram thumbnail, BOM total figure, WAF score
  badge alongside the message that generated them.
- The customer selector (existing `ChatSidebar.tsx`) remains. It sets `customer_id` for
  the session, which drives the memory panel.

---

### Phase 1 Gate Criteria

An SE trial passes when all of the following are true. These are observable without
reading code — the SE can verify each one directly.

| # | Criterion | How verified |
|---|---|---|
| 1 | Every artifact (diagram, BOM, WAF, Terraform, POV, JEP) is requested from the chat input — no form UI opened | SE walks through one full engagement without navigating to a form |
| 2 | Memory panel shows the customer context Archie has built up from the conversation | SE observes the panel update as services and challenge are discussed |
| 3 | WAF review has exactly 6 pillars including Continuous Improvement | SE receives and reads the WAF review |
| 4 | BOM total matches the line-item arithmetic | SE spot-checks: opens the BOM and multiplies one line item |
| 5 | JEP output references `doc_key` (not `artifact_key`) in the summary | SE observes in the Archie response |
| 6 | Tech research and Terraform can be requested in the same session without a port conflict error | SE requests both in sequence |
| 7 | Archie does not re-ask for customer facts already stated in the conversation | SE states sizing once; BOM uses it without asking again |

**Phase 1 failure criteria (any one blocks Phase 2):**

- SE opens a form for any workflow
- SE has to repeat customer context that is already in the memory panel
- WAF review has 5 pillars
- SE corrects a BOM arithmetic error manually
- SE trial feedback is "I'd use this if..." (conditional) rather than "I would use this"

---

## Phase 2 — New Capabilities + Full Second Brain

**Goal:** Add the three net-new capabilities (POC Strategist, Presentation, fan-out) and
the background execution + progress UI that makes parallel artifact generation visible.
This is the "all in" phase — it is only started after Phase 1 SE trial success.

**Gate:** Broader SE trial (multiple SEs, multiple accounts). Automated test suite for
new sub-agents must pass before the SE trial begins.

---

### Backend: New Capabilities

| Capability | New files | Description |
|---|---|---|
| POC Strategist | `sub_agents/poc_strategist/`, `agent/hats/oci_poc_strategist.md`, `agent/tools/specialists.py` (PocStrategistHandler) | 3 parallel angle calls → ranked POC options + recommendation. Two lifecycle paths: explore and confirm. |
| Presentation | `sub_agents/presentation/`, `agent/hats/oci_presentation_writer.md`, `agent/tools/presentation.py` (PresentationHandler) | Deterministic .pptx renderer using Oracle OCI Architecture Toolkit stencil. 7-slide POC kit. |
| Artifact fan-out | `agent/tools/specialists.py` (PocStrategistHandler confirm path) | On confirmation of POC option → `ToolResult(status="parallel", parallel_tools=[5 artifacts])`. Uses existing Forge gather path. |
| Background jobs | `drawing_agent_server.py` (`POST /api/chat/background`), `skillforge/forge.py` (`run_turn_background`) | Fire-and-forget turn execution; poll `GET /api/job/{id}` for status. |
| Telegram notification | `agent/notifications.py` | Implement the existing TODO stub. Fires on background job completion. |

---

### Frontend: Background + Progress

| Component | Change |
|---|---|
| `ChatInterface.tsx` | Add background mode: "Archie is working..." pill while job runs; poll `/api/job/{id}` every 5s; append reply when complete |
| `ChatInterface.tsx` | Fan-out progress: when 5 parallel artifacts are generating, show per-artifact progress indicators (diagram ✓ / BOM ✓ / JEP... / Terraform... / Deck...) |
| `ArtifactPreviewPanel.tsx` | Add `.pptx` download card with presentation thumbnail |

---

### Phase 2 Gate Criteria

| # | Criterion |
|---|---|
| 1 | SE says "what should we build for this customer?" → Archie returns 3 ranked POC options with specific wow moments in < 30 seconds |
| 2 | SE says "go with option 1" → 5 artifacts generate simultaneously; all available for download within ~90 seconds |
| 3 | SE can start a POC planning session, attend a customer meeting, and return to find all artifacts ready (background job) |
| 4 | SE can email the generated .pptx to the customer the same day as the POC planning conversation |
| 5 | New automated tests for poc_strategist and presentation sub-agents pass before trial begins |

---

## What Is Not in Either Phase

These items are explicitly deferred. They are not blocked — they simply require a
deliberate decision to add them to a phase, at which point the golden spec is updated first.

| Item | Why deferred |
|---|---|
| Forge changes | The golden spec defines Forge as out of scope for Archie requirements. Any capability that requires a `skillforge/forge.py` change is a separate architectural decision. |
| Multi-tenancy / user auth changes | Outside the SE second-brain scope. Existing OCI Identity Domain OIDC is sufficient. |
| New OCI service domains beyond current 10 tools | Add to the golden spec as a new domain section first, then implement. |
| A/B testing or analytics instrumentation | Not part of the SE productivity goal. |

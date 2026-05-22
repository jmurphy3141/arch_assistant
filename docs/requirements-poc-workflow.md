# Requirements: Archie POC Workflow

## Overview

The SE team is reorganizing around technical POCs that close deals. The workflow is:

**Rough requirements → 3 parallel POC options → pick + refine → all artifacts in parallel → demo → sale**

This document captures the requirements for extending Archie to support this workflow end-to-end.

---

## Background

Archie today generates artifacts (BOM, diagram, POV, JEP, Terraform) when explicitly asked. It does not:
- Help the SE determine *what to build* for a given customer
- Run in the background while the SE is in a meeting
- Generate all POC artifacts simultaneously
- Produce a client-facing PowerPoint deck

These gaps force SEs to use Archie as a manual artifact generator rather than a deal-closing partner.

---

## Stakeholders

| Role | Name | Interest |
|---|---|---|
| Primary user | SE / Solutions Engineer | Faster POC planning and artifact generation |
| Secondary user | Account Executive | Deal velocity, consistent quality |
| System owner | SE Manager | Scalable POC delivery across team |

---

## Functional Requirements

### FR-1: POC Strategy Generation

**Priority:** P0 — Core new capability

The system must be able to take rough customer requirements and produce a ranked list of POC options.

| ID | Requirement |
|---|---|
| FR-1.1 | Given a customer context (industry, current platform, pain statement, deal stage, timeline), Archie must return exactly 3 POC options |
| FR-1.2 | Each option must be scored on: relevance (1–10), executability (hours to build), cost-effectiveness (narrative), security highlights (list) |
| FR-1.3 | Each option must include: oci_services list, wow_moment description, demo_script_summary |
| FR-1.4 | A recommendation must be provided with rationale citing specific customer inputs — not generic language |
| FR-1.5 | The 3 options must be explored in parallel (3 concurrent sub-agent calls), not sequentially |
| FR-1.6 | If pain_statement or current_platform is absent from context, Archie must ask a single focused clarifying question before proceeding |
| FR-1.7 | The POC Strategist hat must activate automatically before the generate_poc_plan tool is called |

### FR-2: Background Execution

**Priority:** P0 — Required for field use

SEs need to kick off POC generation during or between customer meetings and receive a notification when work completes.

| ID | Requirement |
|---|---|
| FR-2.1 | A new endpoint `POST /api/chat/background` must accept `{message, customer_id}` and return `202 Accepted` with `{job_id}` in under 100ms |
| FR-2.2 | The existing `GET /api/job/{job_id}` endpoint must return `pending` while running, `complete` with `{reply, artifacts}` on success |
| FR-2.3 | On completion, a Telegram notification must fire if `telegram_enabled: true` in config |
| FR-2.4 | The UI must show a "Working in background..." indicator and poll for completion |
| FR-2.5 | Background job results must be appended to the chat history when the user returns |
| FR-2.6 | Background jobs must not block the main SSE streaming path |

### FR-3: Parallel Artifact Generation

**Priority:** P1 — Major time savings

After the SE confirms a POC option, all 5 artifacts must generate simultaneously.

| ID | Requirement |
|---|---|
| FR-3.1 | When the user confirms a POC option (e.g., "go with option 1", "use the migration approach"), all 5 artifacts must start concurrently |
| FR-3.2 | The 5 artifacts are: architecture diagram, BOM, JEP, Terraform, PowerPoint presentation |
| FR-3.3 | All 5 artifacts must be available for download within ~90 seconds of confirmation |
| FR-3.4 | No changes to `skillforge/forge.py` are required — the existing parallel dispatch path handles this |

### FR-4: PowerPoint Presentation

**Priority:** P1 — Required for demo delivery

The final POC deliverable must include a client-facing PowerPoint deck using Oracle's official OCI design standards.

| ID | Requirement |
|---|---|
| FR-4.1 | `generate_presentation` tool must be available in Archie |
| FR-4.2 | Output must be a valid `.pptx` file downloadable via `/download?key=presentation/...` |
| FR-4.3 | Icons must use Oracle OCI stencils from `oracle-oci-architecture-toolkit-v24.1.pptx` — not generic shapes |
| FR-4.4 | The deck must contain exactly 7 slides: title, customer challenge, OCI architecture, key services, cost estimate, implementation timeline, next steps |
| FR-4.5 | Customer name must appear on the title slide |
| FR-4.6 | BOM numbers in the deck must match the generated BOM artifact |
| FR-4.7 | The file must open without errors in Microsoft PowerPoint and Apple Keynote |

---

## Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-1 | Background job must not increase memory footprint by more than 50MB per concurrent job |
| NFR-2 | Parallel artifact fan-out must complete within 120 seconds under normal load (5 concurrent sub-agents) |
| NFR-3 | All new code must follow the existing Forge/Archie boundary: Forge owns orchestration mechanics, Archie owns OCI/SE domain logic |
| NFR-4 | No new LLM calls may be added to `archie_session.py` — all reasoning must flow through `forge.run_turn()` |
| NFR-5 | Telegram notification must be fire-and-forget — failure must not block job completion |
| NFR-6 | Oracle toolkit PPTX must be committed to the repo — not downloaded at runtime |

---

## Architecture Constraints

| Constraint | Rationale |
|---|---|
| Forge (`skillforge/`) changes must be domain-agnostic | Forge is reusable across domains; OCI/SE logic belongs in Archie |
| Sub-agents follow the existing A2A pattern (`server.py` + `system_prompt.md` + `config.yaml`) | Consistency with existing pov, waf, jep, terraform sub-agents |
| New hats follow the `oci_bom_expert.md` format (YAML frontmatter + Markdown sections) | Hat engine expects this format |
| All artifacts use the existing `document_store.save_doc()` + `/download` endpoint | Consistent artifact lifecycle |

---

## Delivery Milestones

### POC Workflow v1

| Issue | Capability | Effort | Dependencies |
|---|---|---|---|
| #1 | Background job + Telegram | 1 day | None |
| #2 | POC Strategist (3 parallel options) | 2 days | None |
| #3 | Parallel artifact fan-out | 0.5 days | Issue #2 |
| #4 | PowerPoint generation | 1 day | None |

Issues #1 and #4 can be worked in parallel. Issue #3 depends on #2.

---

## Acceptance Testing

**POC Strategy:**
- Input: "Customer is an AWS shop with 200-node Kubernetes cluster, CFO flagged $2M/yr cloud bill, exec review in 3 weeks"
- Expected: 3 scored options with recommendation citing "$2M" and "3 weeks" specifically
- Verify: 3 concurrent HTTP calls to poc_strategist sub-agent in server logs

**Background Execution:**
- Trigger: POST `/api/chat/background` with POC request
- Expected: 202 + job_id in <100ms; job transitions to complete within 120s; Telegram fires
- Verify: GET `/api/job/{id}` response progression

**Parallel Fan-out:**
- Trigger: User says "go with option 2" after poc_plan response
- Expected: 5 sub-agent calls start within 1 second of each other (visible in server logs)
- Timing: All 5 complete within 120s total

**PowerPoint:**
- Trigger: `generate_presentation` called with poc_recommendation in memory
- Expected: Valid .pptx at `/download?key=presentation/{customer_id}/v1.pptx`
- Manual verify: Opens in PowerPoint, 7 slides, Oracle OCI icons visible, customer name on title

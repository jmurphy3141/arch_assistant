# Changelog

## [Unreleased] - 2026-07-04

Covers the SkillForge/native-architecture migration and second-brain buildout
(`PLAN.md` Phases 3-7) that landed between the `v1.9.1` tag and today. Grouped
thematically rather than commit-by-commit; see `PLAN.md` and `tasks/p*.md` for
the authoritative per-task record.

### Added

**Native agent loop (Phase 5) — replaces Forge ceremony with native tool-calling**
- `agent/archie_native_loop.py`: native tool-calling ReAct loop gated by
  `orchestrator.agent_mode` in `config.yaml` (`forge` | `native`); the forge
  path and its hat-driven ceremony are untouched behind the flag.
- Hats retired from the native path (Decision #8) — expertise now lives in the
  model, the sub-agent specialists, and reference/lookup tools instead of
  markdown expert lenses; C3E phase is standing identity + memory context.
- Reference/lookup tools for hard facts (compute shapes, compliance
  frameworks, OCI SLAs, Terraform resource reference, customer case studies)
  so producers ground claims by retrieval instead of model recall.
- General-purpose native tools: `read_file_content` (read any stored
  spreadsheet/doc), a deterministic `compute` tool for exact cost/TCO/proration
  math, and `export_artifact` (diagram→PNG, spreadsheet→CSV) with download
  links.
- Generation-discipline fixes: converse/suggest on conversational turns
  instead of auto-generating; draft-first POC/JEP output that never fabricates
  or refuses for missing logistics; honest reporting of `needs_input` tool
  results instead of narrating artifacts that were never produced.
- Live UX productionization (p81/p82): native mode now emits real streaming
  `thinking`/tool-chip events via `reasoning_sink` / `notify(tool_started:*)`,
  isolates per-tool failures so one bad tool call no longer aborts the turn,
  and carries a complete artifact-type map for all 12 registered generate
  tools. `/api/chat/background` now branches on `agent_mode` instead of always
  running the forge path.

**Sub-agent quality harness (Phase 6)**
- `scripts/eval_subagent_quality.py`: deterministic checks + rubric LLM-judge
  distribution + human calibration, producing a per-artifact, per-dimension
  quality baseline (`docs/subagent-quality.json`) across all six specialists.
  A refreshed baseline with real `terraform validate`, producer-variance
  (`--runs 3`), and repo-relative golden paths is in progress (`tasks/p80-*`).

**Second brain — relationship & sales memory layer**
- `archie.relationship` schema in `agent/context_store.py`: stakeholders
  (with disposition), objections, commitments, competitive posture, action
  items, and meeting log per engagement, merged via
  `merge_archie_relationship_facts` (append-with-dedup; competitive
  deep-merges; economic-buyer stakeholder writes through to
  `client_facts.economic_buyer`).
- Debrief loop: `POST /api/notes/upload` → LLM-first relationship-fact
  extraction (`extract_relationship_facts_llm`, regex fallback) → a
  `pending_debrief` checkpoint the SE confirms in chat via `confirm_debrief`
  before anything persists.
- C3E phase tracking (`agent/engagement_mission.py`): correct
  Qualify → Discover → Develop → Design → Prove → Win → Deploy → Support →
  Grow ordering, auto-advance on required-artifact completion, and
  `suggest_next_step` proactivity (overdue commitment → unaddressed objection
  → next artifact), suppressed for 3 turns to avoid nagging.
- Deterministic meeting-prep assembler (`agent/meeting_prep.py`) exposed via
  `GET /api/briefing/{customer_id}/prep`.
- SE cross-account dashboard: `GET /api/se/{se_id}/accounts` reads a per-SE
  engagement index upserted after every turn (`phase`, `blockers`,
  `next_required`, `completed_artifacts`, `last_activity`).

**Expanded specialist bench**
- New A2A sub-agent specialists beyond the original six (bom/diagram/pov/jep/
  waf/terraform): `poc_strategist`, `presentation`, `sales_deck`, `sta`,
  `tech_research`, `technical_proposal` (`sub_agents/*`).
- Domain reference corpus wired into producers and hats: OCI compute shape
  catalog, shape/region availability, provisioning times, service SLAs,
  Terraform resource reference, PCI DSS / HIPAA / FedRAMP control sets, and
  Oracle customer case studies (`agent/standards/`).

**UI — JARVIS dark-ops redesign**
- Cyan (`--accent: #00e5ff`) dark-ops theme replacing the prior orange
  palette; scan-line overlay; activity-only glow (`rail-thinking`,
  `hat-glow-in`, `debrief-wipe-in`, `tool-dot-fire`) so the UI is calm at rest.
- `EngagementMemoryPanel` (per-engagement relationship + artifact view) and
  `MeetingBriefing` (engagement state + `SeAccountsPanel` cross-account table)
  added to the chat UI.

### Changed
- Orchestration fully consolidated onto `skillforge/forge.py` (a domain-agnostic
  ReAct orchestrator: planning → hat gate → expert pre-action → tool dispatch →
  expert post-review → correction loop); `agent/archie_session.py` reduced to a
  thin session wrapper around `forge.run_turn()`; `orchestrator_agent.py` is a
  26-line compatibility shim.
- All generation now routes through `forge.run_turn()` — the
  `_invoke_prerouted_tool` bypass was removed.
- Diagram determinism, handler context hydration/validation, and hat-identity
  transformation hardened across all domain hats.

### Removed
- Dead orchestration paths from the pre-Forge architecture:
  `agent/orchestrator_skill_engine.py`, `agent/skill_loader.py`,
  `agent/langgraph_orchestrator.py`, `agent/langgraph_specialists.py`,
  `agent/graphs/`, `SESSION_CHECKPOINT.md`, and the standalone diagram-form
  UI pages (replaced by chat + `EngagementMemoryPanel`).

### Testing
- `tests/test_archie_native_loop.py`, `tests/test_background_job.py`: native
  loop reasoning/notify hooks, per-tool error isolation, full artifact-type
  coverage, and `agent_mode`-aware background dispatch.
- `tests/test_archie_forge_wiring.py`: guards against orchestration logic
  leaking back into `archie_session.py`.
- Sub-agent quality harness fixtures and golden exemplars for all six
  original specialists (`eval/golden/**`).

## [1.7.0] - 2026-04-22

### Added
- v1.7 BOM service module (`agent/bom_service.py`) with manual refresh-first caches for pricing, compute shapes, and service catalog context.
- BOM REST API surface:
  - `GET /api/bom/config`
  - `GET /api/bom/health`
  - `POST /api/bom/chat`
  - `POST /api/bom/generate-xlsx`
  - `POST /api/bom/refresh-data`
- BOM validation + repair loop enforcing:
  - unknown SKU rejection
  - non-positive price rejection
  - non-GPU compute split rule
  - max 3 repair attempts
- BOM XLSX generation with editable line-item columns and formulas.
- Orchestrator `generate_bom` tool execution in legacy and LangGraph specialist paths.
- Orchestrator fail-closed skill coverage for `bom` path and dynamic skill injection via `gstack_skills/oci_bom_expert`.
- Native React `BOM` tab with advisory/clarify/final flow, editable BOM table, JSON download, XLSX export, and admin refresh action.

### Changed
- Tool trace construction now preserves specialist-provided trace metadata (including BOM trace) in `tool_calls[].result_data.trace`.
- OIDC session user payload now retains `groups` so admin-gated endpoints can enforce global group policy.

### Testing
- Added BOM unit tests (`tests/test_bom_service.py`) for validation and repair behavior.
- Added BOM API integration tests (`tests/test_bom_api.py`) for readiness, refresh, chat, and XLSX flows.
- Added UI BOM tab test coverage in `ui/src/__tests__/App.test.tsx`.

## [1.5.0] - 2026-04-21

### Added
- LangGraph-compatible orchestrator and specialist adapter scaffolding with safe fallback behavior.
- Specialist graph entry modules for diagram, POV, JEP, WAF, and Terraform paths.
- Static vendored `gstack_skills/` placeholders and staged Terraform chain runner.
- Aggregated chat history endpoint (`GET /api/chat/history`) with pagination and search.
- Request trace propagation via `x-trace-id` middleware and response fields.
- Chat streaming endpoint (`POST /api/chat/stream`) with SSE and chunked NDJSON support.
- Streaming event types for `status`, `tool`, `token`, `completion`, `error`, and `terraform_stage`.
- Terraform bundle persistence model and APIs:
  - `POST /api/terraform/generate`
  - `GET /api/terraform/{customer_id}/latest`
  - `GET /api/terraform/{customer_id}/versions`
  - `GET /api/terraform/{customer_id}/download/{filename}`
- Chat response `artifact_manifest` for UI-friendly download link rendering.
- Playwright smoke scaffolding in `ui/` with chat and terraform artifact flow coverage.

### Changed
- `/api/chat` and stream completion payloads now include additive artifact manifest metadata.
- Conversation status tagging now differentiates:
  - `Completed with Terraform`
  - `Terraform Needs Input`
- Terraform UI uses backend bundle metadata and file download API for source rendering.
- Orchestrator now runs explicit combined POV+JEP requests in parallel when no conflicting tool intent is present.

### Testing
- Backend smoke/integration set expanded for:
  - chat history + streaming contracts
  - specialist mode routing
  - terraform graph behavior
  - terraform API endpoints
- UI build validated with Vite.

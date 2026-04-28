# OCI Architecture Assistant
## Master Requirements + Delivery Status (v1.9 Current Baseline)

- Date: April 23, 2026
- Snapshot owner: Implementation working thread
- Master requirements document: `docs/requirements-current-status.md`
- Historical baseline spec: `docs/requirements-v1.5.0.md`
- Source branches merged to `main`: v1.5, v1.6, v1.7, v1.8, and v1.9 doc/UX sync slices merged
- Current working branch: `main`

## 1) Version Baseline

### v1.5.0 (Delivered Baseline)
- Established Agent 0 orchestration baseline, chat-first UI direction, and core POV/JEP/WAF/Terraform/diagram paths.
- Introduced aggregated history, streaming chat surface, and deterministic + nightly quality lanes.

### v1.6.0 (Delivered)
- Dynamic skill discovery/injection across specialist paths via `gstack_skills/*`.
- Bounded critic/refine loop for `generate_pov`, `generate_jep`, `generate_waf`, and `generate_terraform`.
- Layered architecture retained by design:
  - `agent/orchestrator_skills/*`: fail-closed policy/guardrails.
  - `gstack_skills/*`: dynamic prompt guidance and model routing.
- Tool-level tracing stabilized in `tool_calls[].result_data.trace` and structured logs.

### v1.7.0 (Delivered)
- Additive BOM integration delivered end-to-end with shared service, REST API, orchestrator execution path, and native React BOM tab.
- Delivered BOM API surface:
  - `GET /api/bom/config`
  - `GET /api/bom/health`
  - `POST /api/bom/chat`
  - `POST /api/bom/generate-xlsx`
  - `POST /api/bom/refresh-data`
- Shared BOM service and orchestration integration:
  - `agent/bom_service.py` with manual refresh-first cache semantics.
  - `generate_bom` orchestrator tool path for legacy + LangGraph specialist execution.
  - Fail-closed orchestrator skill coverage extended to `bom` path.
  - Dynamic BOM skill injection via `gstack_skills/oci_bom_expert`.

### v1.8.0 (Delivered)
- JEP lifecycle contract and approved-lock revision flow delivered on top of orchestrator hardening baseline:
  - Embedded `jep_state` contract on JEP generate/read endpoints.
  - Approved-lock policy block and explicit revision-request endpoint.
  - JEP lifecycle metadata propagation into orchestrator tool traces.
  - JEP UI lock/revision controls and lifecycle visibility.
- Requirements artifacts:
  - `docs/requirements-v1.8.0-orchestrator-skill-hardening-and-jep-lifecycle.md`
  - `docs/v1.8-jep-skill-hardening-one-pager.md`

### v1.9.0 (Delivered)
- Streaming UX refinement delivered for chat typing/token partial render behavior.
- Documentation synchronized for dynamic specialist skill injection and critic/refine flow.
- Telegram notifier remains intentionally deferred (not in active scope).

## 2) Locked Requirements (Still In Effect)

1. Build in phases, not big-bang.
2. Keep existing external API contracts and A2A behavior.
3. Additive improvements only (streaming/history/UI enhancements).
4. History/artifact persistence remains OCI Object Storage.
5. Playwright required before merge for UI changes.
6. OIDC remains optional.
7. Continue dark OCI chat-first UX direction.
8. Browser-accessed deployments behind restricted ingress must terminate HTTPS on `:443` via reverse proxy/LB and forward to app backend on `:8080`.

## 3) Functional Requirements Status

### 3.1 Backend Orchestration + APIs
- `DONE`: LangGraph orchestrator/specialist routing is active with legacy-safe fallback.
- `DONE`: Terraform staged chain + blocking-question behavior.
- `DONE`: SKILL.md-driven orchestrator pre/post validation across diagram/POV/JEP/WAF/Terraform/summary_document paths (fail-closed).
- `DONE`: Orchestrator SKILL.md governance layer with authoritative block + pushback behavior.
- `DONE`: Fail-closed enforcement for missing/unreadable/malformed required orchestrator skill files.
- `DONE`: Trace ID propagation and structured response trace fields.
- `DONE`: Aggregated history endpoint for sidebar (`/api/chat/history`).
- `DONE`: Chat streaming endpoint with SSE and chunked support (`/api/chat/stream`).
- `DONE`: Terraform artifact persistence + list/latest/download APIs.
- `DONE`: BOM advisory + generation backend surface and shared service integration.
- `DONE`: Existing endpoint compatibility preserved.
- `DONE`: OCI Identity Domain OAuth deployment on systemd with issuer-derived endpoints, secure HTTPS callback cookies, and tracked service template.

### 3.2 UI Requirements
- `DONE`: Chat-first baseline implemented and merged.
- `DONE`: Sidebar with cross-customer history wired to aggregated endpoint.
- `DONE`: Sidebar search/filter/sort and status badge rendering.
- `DONE`: Customer thread switching from sidebar.
- `DONE`: Responsive sidebar toggle for compact/mobile view.
- `DONE`: Accessibility metadata for sidebar controls and navigation.
- `DONE`: Right-side artifact preview panel UX in chat.
- `DONE`: Native BOM tab for advisory/clarify/final flows with editable table + JSON/XLSX export.
- `DONE`: Streaming UX refinement (typing/token partial render behavior).

### 3.3 Testing Requirements
- `DONE`: Backend targeted test suites for history/streaming/routing/terraform.
- `DONE`: BOM unit tests and BOM API integration tests.
- `DONE`: Playwright smoke suite for chat + terraform.
- `DONE`: Playwright sidebar search/switch/status coverage.
- `DONE`: UI BOM tab coverage in React test suite.
- `DONE`: Hybrid test framework markers in pytest config (`unit`, `integration`, `system`, `e2e`, `prompt_static`, `prompt_judge`, `live`).
- `DONE`: Deterministic PR gate command (`./scripts/test_pr_gate.sh`).
- `DONE`: Nightly/manual prompt-quality lane (`./scripts/test_nightly_prompt.sh`) with opt-in `prompt_judge` and optional `live`.
- `DONE`: Recursive prompt-static and prompt-judge suites with scorecard/failure artifacts and pushback-quality coverage.
- `DONE`: Live LLM scenario suite migrated to configured OCI inference path.
- `DONE`: Live server suite pre-validates `AGENT_BASE_URL` reachability and skip-gates invalid hosts.
- `DONE`: Auth/login regression coverage validates OCI Identity Domain authorize redirects.

### 3.4 Explicitly Deferred
- `DEFERRED`: Telegram integration for orchestrator notifications.
  - Rationale: not required for current release goals; keep webhook/bot wiring out of scope until post-v1.9.
- `BACKLOG`: Further provider-specific notification channels beyond the existing logging stub.

## 4) Merge Gate (Current)

1. Deterministic backend gate passes:
   - `./scripts/test_pr_gate.sh`
2. UI e2e/build checks pass for UI-impacting changes.
3. Nightly/manual lane runs recursive prompt judge:
   - `./scripts/test_nightly_prompt.sh`
4. `live` suites remain scheduled/manual opt-in.

## 5) Active Checklists and Requirements References

- Master status: `docs/requirements-current-status.md`
- Baseline requirements (historical): `docs/requirements-v1.5.0.md`
- v1.6 checklist: `docs/v1.6-implementation-checklist.md`
- v1.7 requirements: `docs/requirements-v1.7.0-bom-agent-integration.md`
- v1.7 checklist: `docs/v1.7-implementation-checklist.md`
- v1.8 requirements docs:
  - `docs/requirements-v1.8.0-orchestrator-skill-hardening-and-jep-lifecycle.md`
  - `docs/v1.8-jep-skill-hardening-one-pager.md`
- v1.9 sync notes:
  - `docs/requirements-current-status.md` (this file)

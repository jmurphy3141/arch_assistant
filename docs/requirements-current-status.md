# OCI Architecture Assistant
## Current Requirements + Delivery Status (v1.5 Program Snapshot)

- Date: April 21, 2026
- Snapshot owner: Implementation working thread
- Baseline spec: v1.5.0 overhaul
- Source branches merged to `main`: all active v1.5 slices merged
- Current working branch: `main`

## 1) Locked Requirements (Still In Effect)

1. Build in phases, not big-bang.
2. Keep existing external API contracts and A2A behavior.
3. Additive improvements only (streaming/history/UI enhancements).
4. History/artifact persistence remains OCI Object Storage.
5. Playwright required before merge for UI changes.
6. OIDC remains optional.
7. Continue dark OCI chat-first UX direction.
8. Browser-accessed deployments behind restricted ingress must terminate HTTPS on `:443` via reverse proxy/LB and forward to app backend on `:8080`.

## 2) Functional Requirements Status

### 2.1 Backend Orchestration + APIs
- `DONE`: LangGraph scaffolding for orchestrator/specialists.
- `DONE`: Terraform staged chain + blocking-question behavior.
- `DONE`: Trace ID propagation and structured response trace fields.
- `DONE`: Aggregated history endpoint for sidebar (`/api/chat/history`).
- `DONE`: Chat streaming endpoint modes (`sse` and `chunked`).
- `DONE`: Terraform artifact persistence + list/latest/download APIs.
- `DONE`: Existing endpoint compatibility preserved.

### 2.2 UI Requirements
- `DONE`: Chat-first baseline implemented and merged.
- `DONE`: Sidebar with cross-customer history wired to aggregated endpoint.
- `DONE`: Sidebar search/filter/sort and status badge rendering.
- `DONE`: Customer thread switching from sidebar.
- `DONE`: Responsive sidebar toggle for compact/mobile view.
- `DONE`: Accessibility metadata for sidebar controls and navigation.
- `DONE`: Right-side artifact preview panel UX in chat.
- `NEXT`: Streaming UX refinement (typing/token partial render behavior).

### 2.3 Testing Requirements
- `DONE`: Backend targeted test suites added for history/streaming/routing/terraform.
- `DONE`: Playwright smoke suite for chat + terraform.
- `DONE`: Playwright sidebar search/switch/status coverage.
- `DONE`: Hybrid test framework markers in pytest config (`unit`, `integration`, `system`, `e2e`, `prompt_static`, `prompt_judge`, `live`).
- `DONE`: Deterministic PR gate command (`./scripts/test_pr_gate.sh`) for merge-blocking lanes.
- `DONE`: Nightly/manual prompt-quality lane command (`./scripts/test_nightly_prompt.sh`) with opt-in `prompt_judge` and optional `live`.
- `DONE`: Recursive static prompt-quality suite (`prompt_static`) across orchestrator, diagram, POV, JEP, Terraform, and WAF paths.
- `DONE`: Recursive judge scaffold suite (`prompt_judge`) with scorecard/failure artifact output.
- `DONE`: API-level deterministic system flow test for notes -> orchestrator -> specialists -> persisted history.

## 3) Delivery Timeline by Phase

### Phase 1 (Merged)
- v1.5 backend foundation + initial UI/test integration.
- Merge reference: merged to `main`.

### Phase 2 (Merged)
- Sidebar shell + aggregated history integration + thread switching.
- Merge reference: merged to `main`.

### Phase 3 (Merged)
- Responsive sidebar polish + accessibility + status badge e2e checks.
- Merge reference: merged to `main`.

### Phase 4 (Merged)
- Artifact preview panel integration and associated checks merged to `main`.

## 4) Merge Gate (Current)

Current merge gate policy:

1. Deterministic backend gate passes:
   - `./scripts/test_pr_gate.sh`
2. UI e2e/build checks pass for UI-impacting changes.
3. Nightly/manual lane runs recursive prompt judge:
   - `./scripts/test_nightly_prompt.sh`
4. `live` suites remain scheduled/manual opt-in.

## 5) Immediate Next Work Items (Ordered)

1. Expand prompt-judge from heuristic scaffold to LLM-judge integration for nightly/manual lanes.
2. Add deeper mocked UI e2e coverage for artifact preview and streaming edge cases.
3. Continue streaming UX polish (token/typing partial render behavior).
4. Keep requirements/status snapshots synchronized with each merged slice.

---

This document is the active execution reference for current implementation status and next actions. For original baseline constraints, also see:
- `docs/requirements-v1.5.0.md`
- `docs/v1.5-migration-plan.md`

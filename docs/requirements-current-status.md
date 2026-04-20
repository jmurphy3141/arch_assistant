# OCI Architecture Assistant
## Current Requirements + Delivery Status (v1.5 Program Snapshot)

- Date: April 19, 2026
- Snapshot owner: Implementation working thread
- Baseline spec: v1.5.0 overhaul
- Source branches merged to `main`: PR #1, PR #2, PR #3
- Current working branch: `codex/v1-5-phase4-artifacts-and-streaming`

## 1) Locked Requirements (Still In Effect)

1. Build in phases, not big-bang.
2. Keep existing external API contracts and A2A behavior.
3. Additive improvements only (streaming/history/UI enhancements).
4. History/artifact persistence remains OCI Object Storage.
5. Playwright required before merge for UI changes.
6. OIDC remains optional.
7. Continue dark OCI chat-first UX direction.

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
- `IN PROGRESS` (Phase 4): Right-side artifact preview panel UX in chat.
- `NEXT`: Streaming UX refinement (typing/token partial render behavior).

### 2.3 Testing Requirements
- `DONE`: Backend targeted test suites added for history/streaming/routing/terraform.
- `DONE`: Playwright smoke suite for chat + terraform.
- `DONE`: Playwright sidebar search/switch/status coverage.
- `IN PROGRESS`: Playwright artifact panel flow coverage (new Phase 4 work).

## 3) Delivery Timeline by Phase

### Phase 1 (Merged)
- v1.5 backend foundation + initial UI/test integration.
- Merge reference: PR #1 into `main`.

### Phase 2 (Merged)
- Sidebar shell + aggregated history integration + thread switching.
- Merge reference: PR #2 into `main`.

### Phase 3 (Merged)
- Responsive sidebar polish + accessibility + status badge e2e checks.
- Merge reference: PR #3 into `main`.

### Phase 4 (Current Active Work)
- Branch: `codex/v1-5-phase4-artifacts-and-streaming`
- Current slice: artifact preview panel integration into chat mode.
- Local in-progress files at snapshot:
  - `ui/src/App.tsx`
  - `ui/src/components/ChatInterface.tsx`
  - `ui/src/components/ArtifactPreviewPanel.tsx` (new)
  - `ui/e2e/chat-and-terraform.spec.ts`

## 4) Merge Gate (Current)

Before Phase 4 PR:

1. `npm.cmd run e2e` passes with artifact-panel assertions.
2. `npm run build` passes.
3. Branch is clean (no `ui/node_modules` noise).
4. PR description includes:
   - scope of artifact panel behavior,
   - fallback behavior,
   - e2e evidence.

## 5) Immediate Next Work Items (Ordered)

1. Finalize artifact preview panel behavior and state sync in chat mode.
2. Stabilize e2e for artifact panel open/select/download expectations.
3. Add streaming UX polish slice (token/typing partial render improvements).
4. Open Phase 4 PR and merge after checks.

---

This document is the active execution reference for current implementation status and next actions. For original baseline constraints, also see:
- `docs/requirements-v1.5.0.md`
- `docs/v1.5-migration-plan.md`

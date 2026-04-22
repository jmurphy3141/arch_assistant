# OCI Architecture Assistant
## v1.7.0 Requirements — BOM Engine Integration (Agent-Style)

- Date: April 22, 2026
- Owner: Jason Murphy
- Version target: v1.7.0
- Status: Approved requirements draft

## 1) Objective

Integrate the imported OCI BOM engine capabilities into the current agent platform while preserving the existing product UX, auth model, and orchestration patterns.

The imported backend BOM behavior is in scope. The imported standalone UI is not.

## 2) Locked Constraints

1. Keep additive-only implementation; no big-bang replacement.
2. Preserve existing app UI style and interaction model.
3. Preserve existing auth/session standards in current app.
4. Preserve existing orchestrator and A2A contracts.
5. UI changes require Playwright coverage before merge.

## 3) Scope Decision

### In scope

1. Port BOM backend logic (chat classification, BOM validation/repair, pricing enforcement, XLSX generation).
2. Port BOM data refresh and cache semantics (price list + shapes + services).
3. Add BOM API surface under current backend namespace (`/api/bom/*`).
4. Build BOM UX in existing React UI only.
5. Add tracing and logs aligned with current backend conventions.

### Out of scope

1. Shipping imported standalone `index.html` / `admin.html` as product UI.
2. Running a separate BOM-specific OIDC login stack in production UX.
3. Replacing existing app shell/navigation design.

## 4) Required API Contract (Target)

1. `GET /api/bom/config`
2. `POST /api/bom/chat`
3. `POST /api/bom/generate-xlsx`
4. `POST /api/bom/refresh-data`
5. `GET /api/bom/health`

Response semantics should mirror the imported BOM engine behavior:
- `type`: `normal | question | final`
- `reply`
- optional `json_bom`, `bom_payload`, `score`

## 5) BOM Logic Requirements

1. Price list is authoritative and sourced from Oracle pricing API cache.
2. Compute shape guidance sourced from scraped OCI docs cache.
3. Service catalog guidance sourced from scraped OCI docs cache.
4. BOM validation rules enforced before finalization:
- no unknown SKU in final BOM
- no non-positive prices
- non-GPU compute split rules enforced
5. Repair/retry loop preserved (up to 3 attempts).
6. XLSX generation includes formulas and editable line-item schema compatibility.

## 6) UI Requirements (Current UI)

1. Add BOM workflow as a native feature within existing UI app.
2. Maintain existing design language, theme, and navigation model.
3. Support:
- advisory chat
- clarifying question loop
- final BOM review/edit table
- JSON download
- XLSX export
4. Avoid standalone static HTML shipping for end users.

## 7) Auth + Security Requirements

1. Use current app authentication and authorization flow.
2. Protect BOM mutation/admin endpoints (`refresh-data`, exports as needed).
3. Do not enforce import-time BOM-specific OIDC validation in shared server startup path.
4. Keep sensitive config in environment, not hardcoded.

## 8) Observability Requirements

Every BOM request should include trace-aligned metadata in logs and/or responses:

1. trace_id
2. selected model_id
3. result mode (`normal|question|final`)
4. validation/repair attempts
5. cache source and readiness
6. request latency

## 9) Testing Requirements

### Unit
1. pricing parse/enforcement
2. BOM schema normalization/validation
3. relaxed JSON extraction and repair paths
4. XLSX output formulas/columns sanity checks

### Integration
1. `/api/bom/chat` normal path
2. `/api/bom/chat` question path
3. `/api/bom/chat` final path
4. `/api/bom/generate-xlsx`
5. `/api/bom/refresh-data`

### E2E
1. Existing UI BOM workflow happy path
2. Clarify -> final -> edit -> export flow
3. No regression in existing orchestrator/chat flows

## 10) Merge Gate

1. Existing deterministic gates remain green.
2. New BOM unit/integration tests green.
3. Playwright BOM tests green.
4. Existing UI/orchestrator regressions: none.

## 11) Implementation Strategy

1. Phase A: Port BOM backend logic into agent/server modules.
2. Phase B: Expose `/api/bom/*` contracts.
3. Phase C: Build native UI experience in current frontend.
4. Phase D: Add tracing + tests + docs.
5. Phase E: Release as v1.7.0.

## 12) Reference Material

Imported source examples are stored under:

- `docs/examples/bom-source-drop/main.py.example`
- `docs/examples/bom-source-drop/index.html.example`
- `docs/examples/bom-source-drop/admin.html.example`
- `docs/examples/bom-source-drop/README.external.example.md`

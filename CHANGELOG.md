# Changelog

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

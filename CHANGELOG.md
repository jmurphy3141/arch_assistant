# Changelog

## [1.5.0] - Unreleased

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

### Testing
- Backend smoke/integration set expanded for:
  - chat history + streaming contracts
  - specialist mode routing
  - terraform graph behavior
  - terraform API endpoints
- UI build validated with Vite.


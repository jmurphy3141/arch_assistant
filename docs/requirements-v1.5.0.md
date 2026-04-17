# OCI Architecture Assistant
## Requirements Specification v1.5.0

- Project: OCI Architecture Assistant
- Version: 1.5.0
- Date: April 17, 2026
- Status: Approved baseline for implementation
- Target first deployable build: April 24, 2026

## Summary

Version 1.5.0 is a phased major rebuild with:

- LangGraph-based orchestration and specialist agents.
- Terraform migrated to static vendored gstack chain.
- Modern Grok/Claude-style chat UI with cross-customer history.
- New streaming chat endpoints while preserving existing contracts.
- Backward compatibility for all existing endpoints.

## Locked Product Decisions

- Build in phases, not big-bang.
- Keep all existing endpoints/behaviors.
- Add streaming via both SSE and chunked JSON using new endpoint(s).
- Keep `/api/chat` unchanged for existing clients.
- Add aggregated cross-customer history endpoint.
- Source of truth for history/artifacts remains OCI Object Storage.
- Single thread per customer in v1.5.0.
- Attachments upload on send.
- `.drawio` preview: rendered preferred, XML fallback.
- Specialist agents are independent graphs.
- Orchestrator routes aggressively in parallel where safe.
- Terraform gstack chain blocks completion on failed stage and asks questions.
- gstack skills are static files vendored in repo.
- Per-agent model endpoints/config are defined in config.
- Observability baseline: structured logs + trace ID.
- Global retry/timeout defaults plus per-agent overrides.
- OIDC remains optional.
- UI stack is `shadcn/ui` + Tailwind.
- Legacy forms remain under an `Advanced` tab.
- Merge gate must include Playwright.
- Tests are reorganized.
- Release with version bump, changelog, and tag `v1.5.0`.
- Implementation branch is `codex/v1-5-langgraph-rebuild`.

## Architecture Requirements

- Agent 0 becomes a LangGraph supervisor.
- Diagram, POV, JEP, WAF, and Terraform each become independent LangGraph modules.
- External Oracle Agent Spec v26.1.0 A2A v1.0 compliance remains unchanged.
- Existing A2A/REST external routes remain operational.

## API Requirements

- Preserve existing endpoint contracts.
- Add a new streaming endpoint namespace for chat.
- Streaming payloads include:
  - `trace_id`
  - `customer_id`
  - `event_type`
  - completion/error metadata
- Add aggregated history endpoint with pagination and search.

## Config Requirements

- Add global LLM defaults.
- Add per-agent overrides:
  - endpoint
  - model
  - temperature/tokens
  - retry/timeout
- Terraform path configured for Grok Code endpoint/model.
- Orchestrator may use a separate reasoning-focused endpoint/model.

## Observability and Reliability

- Attach/generate trace ID for every request.
- Propagate trace ID across orchestrator/specialist calls.
- Log trace ID, endpoint/agent, customer_id (when available), and errors.
- Support global retries/timeouts with per-agent overrides.

## UI/UX Requirements

- Grok/Claude-style dark chat UX using shadcn + Tailwind.
- Sidebar: cross-customer history, search/filter, chat switching.
- Main chat: token streaming, markdown, code copy, quick actions.
- Keep legacy workflows in an `Advanced` tab.
- Accessibility baseline for new surfaces: WCAG 2.1 AA.

## Testing and Release Gates

- Reorganized unit/integration/e2e tests.
- Playwright required before merge.
- Existing API compatibility smoke checks must pass.
- Update version to `v1.5.0`.
- Add changelog entry and release tag.

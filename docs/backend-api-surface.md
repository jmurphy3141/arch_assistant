# Backend API Surface

Last updated: 2026-06-01.

`drawing_agent_server.py` is still the FastAPI composition root. It owns app
startup, middleware, auth/session setup, static UI serving, health/config routes,
and compatibility routes. Route extraction should preserve the status labels below
until the corresponding UI and tests are moved.

## Primary Chat APIs

These are the product path for Archie.

| Route | Status | Notes | Guarding tests |
|---|---|---|---|
| `POST /api/chat` | primary | Non-streaming chat turn through `agent.archie_session.run_turn()` and Forge. | `tests/test_chat_history_streaming.py`, `tests/test_orchestrator_decision_flow.py` |
| `POST /api/chat/stream` | primary | SSE/chunked streaming chat through `agent.chat_stream`. | `tests/test_chat_history_streaming.py` |
| `POST /api/chat/background` | primary | Background Forge turn with pollable job result. | `tests/test_background_job.py` |
| `GET /api/job/{job_id}` | primary | Poll background/async job state. | `tests/test_background_job.py`, UI chat tests |
| `GET /api/chat/{customer_id}/history` | primary | Loads customer conversation history. | `tests/test_chat_history_streaming.py` |
| `GET /api/chat/history` | primary | Paginated conversation index. | `tests/test_chat_history_streaming.py` |
| `GET /api/chat/projects` | primary | Project grouping for the sidebar. | `tests/test_chat_history_streaming.py` |
| `DELETE /api/chat/{customer_id}/history` | primary | Clears chat history. | UI chat tests |
| `POST /api/chat/{customer_id}/reset-context` | primary | Resets active memory without deleting artifacts. | `tests/test_chat_history_streaming.py` |

## A2A And Discovery

| Route | Status | Notes | Guarding tests |
|---|---|---|---|
| `GET /.well-known/agent.json` | primary | A2A agent card. | `tests/test_a2a.py` |
| `GET /.well-known/agent-card.json` | compatibility | Legacy alias for discovery clients. | `tests/test_a2a.py` |
| `POST /message:send` | primary | JSON-RPC/A2A message entrypoint. | `tests/test_a2a.py` |
| `GET /tasks/{task_id}` | primary | A2A task lookup. | `tests/test_a2a.py` |
| `POST /tasks/{task_id}:cancel` | primary | A2A task cancel. | `tests/test_a2a.py` |
| `POST /api/a2a/task` | compatibility | Internal task-style API. | route smoke coverage |

## Compatibility Artifact APIs

These routes are still used by specialist form components, legacy tests, or direct
artifact workflows. Do not remove them until the UI and tests are migrated to chat.

| Route group | Status | Notes | Guarding tests |
|---|---|---|---|
| `/api/generate`, `/api/upload-bom`, `/api/clarify`, `/api/refine` | compatibility | Direct diagram generation and refinement path. | `server/tests/test_api.py`, `tests/scenarios/test_scenarios.py`, `ui/src/__tests__/App.test.tsx` |
| `/download`, `/api/download`, `/download/{filename}`, `/api/download/{filename}` | compatibility | Scoped diagram/artifact downloads. | `server/tests/test_api.py`, scenario tests |
| `/api/bom/config`, `/api/bom/health`, `/api/bom/chat`, `/api/bom/generate-xlsx`, `/api/bom/refresh-data`, `/api/bom/{customer_id}/download/{filename}` | compatibility | Direct BOM advisor and XLSX export path. | `tests/test_bom_api.py`, `tests/test_chat_history_streaming.py` |
| `/api/notes/upload`, `/api/notes/{customer_id}` | compatibility | Direct note upload/list path; chat upload also depends on it. | `tests/test_server_live.py`, UI chat tests |
| `/api/pov/*` | compatibility | Direct POV generation, approval, latest/version lookup. | `tests/test_server_live.py`, UI form tests |
| `/api/jep/*` | compatibility | Direct JEP generation, approval, revision, kickoff, Q&A. | `tests/test_jep_lifecycle.py`, `tests/test_server_live.py`, UI form tests |
| `/api/waf/*` | compatibility | Direct WAF generation and lookup. | `tests/test_server_live.py`, UI form tests |
| `/api/terraform/*` | compatibility | Direct Terraform generation, lookup, and file download. | `tests/test_terraform_api.py`, `tests/test_server_live.py`, UI form tests |
| `/api/context/{customer_id}` | compatibility | Context inspection for UI/debugging. | `tests/test_server_live.py` |

## Extraction Guidance

- Extract route groups only with behavior-preserving moves to `APIRouter`
  modules.
- Keep `drawing_agent_server.py` as the app factory/composition root until a
  separate task defines a new app package.
- Use the test groups above as the minimum compatibility gate for any route move.
- Public removal requires a separate deprecation task; this inventory only labels
  route ownership and migration status.

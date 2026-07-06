# AGENTS.md

Last updated: 2026-07-06 for Archie OCI Architecture Assistant. The full p0-p82
task sequence is landed (`PLAN.md` Phases 3-7 enablers all `done`; see
`CHANGELOG.md` "Unreleased" for the full list). Phase 6/7 "then" follow-on work
(sub-agent tuning, outcome capture, cross-client corpus) is authorized but not
yet spec'd into task files.

Read this file first, then read `PLAN.md` before touching any code.
`PLAN.md` is the locked architecture plan. It defines the target state,
the phase sequence, and what Codex must never do. If a task conflicts
with `PLAN.md`, stop and flag it — do not improvise.

`CLAUDE.md` is the codebase reference.

## Repo Snapshot

- Product: Archie OCI Architecture Assistant.
- Runtime: FastAPI backend serving a React/Vite UI.
- Main backend app: `drawing_agent_server.py`.
- Main UI app: `ui/src/App.tsx`.
- Agent 0 orchestrates chat, uploaded notes, diagrams, BOM, POV, JEP, WAF,
  Terraform, tech research, POC strategy, presentations, sales decks, STA,
  and technical proposal workflows.
- Two orchestration paths behind `config.yaml` → `orchestrator.agent_mode`
  (defaults to `"forge"`): **forge** (`skillforge/forge.py`, hat-gated ReAct
  loop) and **native** (`agent/archie_native_loop.py`, native tool-calling,
  no hats — Decision #8). Forge behavior must stay byte-for-byte unchanged
  by any native-loop work.
- `archie.memory` is the canonical specialist execution contract and is
  injected into BOM, Diagram, WAF, Terraform, POV, and JEP prompts (both
  orchestration paths).
- v1.9 completion evidence and guardrail status live in
  `docs/v1_9_status.md` (historical snapshot — the codebase has moved well
  past v1.9 since; treat it as evidence of what shipped by that date, not a
  current-state doc).
- Backend route ownership and compatibility status live in
  `docs/backend-api-surface.md`.
- Expert hats live under `agent/hats/` (19 files, forge path only); specialist
  services live under `sub_agents/` (12 directories).
- Production service runs uvicorn on internal port `8080`.

## Read First

- Start with this file, then inspect only the files relevant to the task.
- Preserve dirty user changes. Do not clean or restore unrelated files.
- Keep patches scoped to the requested behavior.
- Prefer existing helper modules and local patterns over new abstractions.
- Update this file when changing architecture, commands, deployment, or major
  workflows in a way that would make this guide misleading.

## Do Not Waste Time

- Avoid `ui/node_modules/`; it is dependency output and may be dirty.
- Avoid `ui/dist/`; it is Vite build output.
- Avoid `__pycache__/`, `.pytest_cache/`, coverage, log, and other cache/build
  output unless the task is explicitly about them.
- Treat `CLAUDE.md` as historical onboarding, not the first-read source.
- Do not scan generated `.drawio` fixtures unless layout/output behavior is the
  task.

## Primary Entrypoints

- `drawing_agent_server.py`: FastAPI composition root, config, middleware,
  auth/session setup, static UI serving, startup, and legacy diagram routes.
- `server/models.py`: FastAPI/Pydantic request and response models.
- `server/routes/`: APIRouter modules for chat, BOM, documents, and A2A.
- `server/services/`: server-only job and BOM artifact/download helpers.
- `agent/orchestrator_agent.py`: thin compatibility shim for existing Agent 0
  imports.
- `agent/archie_session.py`: session wrapper and compatibility layer — reads
  `get_agent_mode()`, dispatches to `forge.run_turn()` or
  `archie_native_loop.run_turn()`, saves results, and still contains
  deterministic fast paths plus legacy tool-dispatch compatibility (forge path).
- `agent/archie_wiring.py`: `build_forge()` — constructs the Forge instance
  with the Archie system prompt and registers all 16 OCI tool handlers.
- `agent/archie_native_loop.py`: native tool-calling loop for
  `agent_mode: native` — no hat gate, no expert pre-action/post-review; adds
  its own tool surface (below) for grounding instead.
- `agent/archie_memory_retrieval.py`, `agent/reference_tools.py`,
  `agent/file_reader_tools.py`, `agent/compute_tools.py`,
  `agent/export_tools.py`, `agent/semantic_notes.py`: native-only tool specs —
  memory recall/search, reference-data lookups, arbitrary stored-file reads,
  deterministic compute, artifact export (PNG/CSV), and `semantic_search`
  (meaning/paraphrase retrieval over one engagement's transcript index),
  respectively.
- `agent/transcript_ingest.py`, `agent/embedding_client.py`: transcript
  distillation (reuses the debrief/confirm_debrief path — nothing persists to
  `archie.relationship` until confirmed) plus chunk/embed/index into an
  isolated per-engagement store. `POST /api/notes/upload` with
  `is_transcript=true` routes here instead of the generic note path; raw
  transcript text never reaches `read_file_content` or a specialist prompt.
- `agent/archie_memory.py`: context assembly, memory enforcement, BOM
  hydration, and specialist-question management.
- `agent/hat_engine.py`: loads markdown hats and exposes hat activation tools
  for Archie (forge path only — native mode never activates hats).
- `agent/safety_rules.py`: thin deterministic safety guard for hard blocks.
- `agent/document_store.py`: generated artifacts and document persistence.
- `agent/context_store.py`: per-client/customer context, relationship schema
  (`archie.relationship`), and uploaded note state.
- `agent/engagement_mission.py`: C3E phase tracker and `suggest_next_step`
  proactivity.
- `agent/meeting_prep.py`: deterministic `build_meeting_prep()` assembler.
- `agent/decision_context.py`: per-turn Decision Context extraction,
  constraint tags, and deterministic summaries.
- `agent/consistency_contract.py`: canonical selected-POC, BOM, and Diagram
  component identities, assumptions, artifact bindings, and parity validation.
- `agent/bom_service.py`: BOM parsing, validation, readiness, and repair flows.
- `agent/jep_lifecycle.py`: JEP draft/review lifecycle state.
- `agent/jep_composer.py`: validated grounded JEP brief extraction, canonical
  Markdown composition, revision handling, and deterministic validation.
- `agent/poc_composer.py`: grounded POC brief extraction and deterministic
  three-option composition with presentation-only LLM polishing.
- `server/routes/chat.py`: `/api/chat`, `/api/chat/stream`, and
  `/api/chat/background` — the background route branches on
  `archie_session.get_agent_mode()` the same way the session wrapper does.
- `server/routes/briefing.py`: `/api/briefing/*` — engagement state, SE
  cross-account accounts, meeting prep.
- `ui/src/App.tsx`: tab shell and top-level UI state.
- `ui/src/components/ChatInterface.tsx`: chat experience and Agent 0 surface.
- `ui/src/api/client.ts`: browser API client and endpoint contracts.
- `ui/src/__tests__/`: Vitest/MSW UI coverage.
- `tests/`: Python unit, integration, prompt, and live opt-in tests.

## Architecture Map

- Backend API: `drawing_agent_server.py` composes the FastAPI app and registers
  route modules from `server/routes/`; direct upload, generate, clarify,
  refine, download, health, config, and refresh routes remain in the composition
  root.
- Static UI: the backend serves the Vite build from `ui/dist/` in production.
- Orchestrator: `skillforge/forge.py` owns the ReAct loop — planning, hat
  activation, expert pre-action, tool dispatch, expert post-review, and
  correction — on the **forge** path (`agent_mode: forge`, the default).
  `agent/archie_session.py` loads state, calls `forge.run_turn()`, saves
  results, and preserves compatibility fast paths while migration work
  continues.
- **Native path** (`agent_mode: native`): `agent/archie_native_loop.py` runs a
  native tool-calling loop instead — no hat gate, no expert pre/post-review.
  It registers the same domain tools plus native-only grounding tools
  (`recall_fact`, `search_notes`, `get_decisions`, `list_artifacts`,
  `get_meeting_summaries`, `lookup_compute_shapes`, `lookup_price`,
  `lookup_reference_architecture`, `read_file_content`, `compute`,
  `export_artifact`, `semantic_search`). Per-tool failures are isolated (a raising handler
  produces a `status="error"` ToolResult; the turn continues). `reasoning_sink`
  and `notify("tool_started:<tool>", ...)` drive the same live thinking/
  tool-chip UI events the forge path already produced. Changing the native
  loop must never change forge-path behavior, and vice versa.
- ReAct prompts include internal orchestrator self-guidance; deterministic fast
  paths skip ReAct by design and are not self-guidance failures. This applies
  to the forge path only — the native path has no ReAct prompt to check.
- Decision Context is generated per turn, persisted to context, included in
  traces, and recorded in the Decision Log.
- Canonical Archie memory is assembled and enforced in `agent/archie_memory.py`,
  then refreshed after user turns, saved notes, and specialist results.
  Specialist tool arguments include `_memory_snapshot`, and final specialist
  prompts must contain `[Archie Canonical Memory]`.
- Management Summary rendering is deterministic and consolidates refinements,
  safety review, tradeoffs, artifact refs, and checkpoint status.
- Safety enforcement applies deterministic hard-block rules before artifact
  exposure.
- JEP generation is deterministic and grounded: incomplete briefs return
  kickoff questions, complete briefs render canonical Markdown and DOCX, and
  lifecycle state advances only after both artifacts persist.
- POC exploration composes three grounded options, confirmation records only
  the selected option, and downstream POV/BOM/JEP artifacts run only when the
  user explicitly requests them.
- Selected POC decisions are binding downstream. BOM and Diagram generation
  validate canonical component, database, sizing, HA, and connectivity parity
  before exposing artifacts; conflicts require a confirmed impact update.
- Archie expert review wraps shared tool calls after specialist execution and
  before artifact exposure. It records the selected lens, sanitized specialist
  input, review verdict/findings, and retry history in
  `tool_calls[].result_data.trace`.
- BOM finalization is fail-closed for explicit sizing mismatches. If requested
  OCPU, RAM, or storage is larger than `bom_payload.line_items`, Archie retries
  once when safe and otherwise blocks XLSX persistence/download exposure.
- BOM handoff uses an internal A2A-shaped `generate_bom.inputs` wrapper.
  Archie extracts region, architecture option, compute, memory, storage,
  connectivity, DR, workload, OS mix, and output format from canonical memory
  and current-turn facts; the BOM service converts those structured fields into
  the existing validated payload/XLSX flow.
- Persistence: document/context stores write local artifacts and can integrate
  with OCI Object Storage through `agent/object_store_oci.py` and related
  persistence modules.
- Hats: markdown files in `agent/hats/` (19 total) encode Archie's expert
  lenses — forge path only; never activated by the native loop.
- Sub-agents: `sub_agents/` contains 12 independent A2A services: BOM,
  diagram, POV, JEP, WAF, Terraform, tech research, sales deck, POC strategy,
  presentation, STA, and technical proposal.
- Second brain: `archie.relationship` (stakeholders, objections, commitments,
  competitive posture, action items, meetings) lives in
  `agent/context_store.py`; the debrief loop (`server/routes/documents.py` →
  `extract_relationship_facts_llm()` → `pending_debrief` → `confirm_debrief`
  tool) and C3E phase tracking (`agent/engagement_mission.py`) are
  Archie-domain only — `skillforge/` has zero knowledge of any of it.
- Individual specialist endpoints can be overridden at runtime with
  `ARCHIE_SUB_AGENT_<NAME>_URL` (for example,
  `ARCHIE_SUB_AGENT_DIAGRAM_URL=http://127.0.0.1:18082`).
- React UI: `App.tsx` coordinates tabs; form components call typed helpers in
  `ui/src/api/client.ts`; chat lives in `ChatInterface.tsx`.
- Tests: Python tests use `pytest.ini` markers; UI tests use Vitest,
  Testing Library, and MSW handlers. `.github/workflows/non-live-tests.yml`
  runs `pytest -m "not live"` for pushes and pull requests.
- Deployment: `Dockerfile` and `deploy/oci-agent.service` run
  `drawing_agent_server:app` with uvicorn on port `8080`.

## Common Commands

Use focused commands first. Broaden only when the touched surface justifies it.

```bash
# UI
cd ui && npm run test -- ChatInterface
cd ui && npm run test -- App
cd ui && npm run typecheck
cd ui && npm run build
cd ui && npm run dev -- --host 0.0.0.0 --port 4173

# Python syntax/import smoke
python3.11 -m compileall drawing_agent_server.py agent tests

# Focused pytest examples
pytest tests/test_specialist_mode_routing.py -v
pytest tests/test_orchestrator_decision_flow.py -v
pytest tests/test_orchestrator_parallel_reply.py tests/test_decision_context.py -v
pytest tests/test_bom_service.py tests/test_bom_api.py -v
pytest tests/test_terraform_api.py -v
pytest tests/test_jep_lifecycle.py -v
pytest tests/test_sub_agent_port_config.py -v
pytest tests/test_archie_forge_wiring.py -v          # forge/session boundary guard
pytest tests/test_archie_native_loop.py -v           # native loop (agent_mode: native)
pytest tests/test_background_job.py -v               # /api/chat/background, both agent_mode values
pytest tests/test_transcript_memory.py -v             # transcript distill/confirm/semantic-index

# Repo gates
./scripts/test_pr_gate.sh -v
./scripts/test_nightly_prompt.sh -v
PROMPT_JUDGE_STRICT=0 ./scripts/test_nightly_prompt.sh -v

# Live opt-in only
RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_live.py -v -s
AGENT_BASE_URL=http://127.0.0.1:8080 pytest tests/test_server_live.py -v -s

# General SE qualification (point only at an isolated current-source stack)
python3.11 scripts/qualify_general_se.py --base-url http://127.0.0.1:18080
python3.11 scripts/qualify_general_se.py --suite complex-three-tier --base-url http://127.0.0.1:18080

# Engagement purge is dry-run by default. Supply explicit keep IDs, review all
# three reports, and never run qualification against the purged environment.
python3.11 scripts/purge_engagements.py --keep-customer-id <real-customer-id>
```

## Run And Health Check

```bash
python3.11 -m uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 --reload
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/api/bom/health
```

For production-style restart on the OCI host, prefer the service:

```bash
sudo systemctl restart oci-agent.service
sudo systemctl is-active oci-agent.service
```

If serving a new UI build through FastAPI, rebuild the UI first:

```bash
cd ui && npm run build
sudo systemctl restart oci-agent.service
```

## Workflow Rules

- Read `AGENTS.md` before broad repo exploration.
- Use `rg` and `rg --files` for navigation.
- Inspect the smallest relevant file set before editing.
- Keep docs-only changes docs-only; no runtime test is required for this file.
- For frontend changes, run focused Vitest plus `typecheck` when practical.
- For backend route/orchestrator changes, run the nearest pytest files and a
  Python compile smoke.
- For deployment changes, verify the service command, port, and health route.
- Never commit secrets, local `.env` values, logs, or generated dependency
  output.
- When touching files already modified by someone else, preserve their edits
  and adapt around them.

## Maintenance Note

Agents must update this file whenever their changes would make its repo map,
commands, deployment notes, or workflow rules inaccurate.

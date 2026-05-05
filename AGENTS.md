# AGENTS.md

Last updated: 2026-04-30 for Archie OCI Architecture Assistant v1.9.x.

Read this file first, then read `PLAN.md` before touching any code.
`PLAN.md` is the locked architecture plan. It defines the target state,
the phase sequence, and what Codex must never do. If a task conflicts
with `PLAN.md`, stop and flag it — do not improvise.

`CLAUDE.md` is the codebase reference. `SESSION_CHECKPOINT.md` is stale — ignore it.

## Repo Snapshot

- Product: Archie OCI Architecture Assistant.
- Runtime: FastAPI backend serving a React/Vite UI.
- Main backend app: `drawing_agent_server.py`.
- Main UI app: `ui/src/App.tsx`.
- Agent 0 orchestrates chat, uploaded notes, diagrams, BOM, POV, JEP, WAF,
  and Terraform workflows.
- `archie.memory` is the canonical specialist execution contract and is
  injected into BOM, Diagram, WAF, Terraform, POV, and JEP prompts.
- v1.9 completion evidence and guardrail status live in
  `docs/v1_9_status.md`.
- Local specialist instructions live under `agent/orchestrator_skills/` and
  `gstack_skills/`.
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
- Treat `SESSION_CHECKPOINT.md` as stale session residue unless requested.
- Treat `CLAUDE.md` as historical onboarding, not the first-read source.
- Do not scan generated `.drawio` fixtures unless layout/output behavior is the
  task.

## Primary Entrypoints

- `drawing_agent_server.py`: FastAPI app, API routes, static UI serving, server
  orchestration glue.
- `agent/orchestrator_agent.py`: thin compatibility shim for existing Agent 0
  imports.
- `agent/archie_loop.py`: Agent 0 chat loop, tool execution, specialist mode
  routing, intent classification, prompt builders, artifact replies, and
  high-level workflow decisions.
- `agent/archie_memory.py`: canonical Archie memory/context assembly, BOM
  handoff hydration, specialist-question management, and memory contract
  enforcement used by the orchestrator.
- `agent/document_store.py`: generated artifacts and document persistence.
- `agent/context_store.py`: per-client/customer context and uploaded note state.
- `agent/orchestrator_skill_engine.py`: skill loading/execution support for
  orchestrator skills.
- `agent/decision_context.py`: per-turn Decision Context extraction,
  constraint tags, and deterministic summaries.
- `agent/governor_agent.py`: deterministic governor/critic evaluation,
  security and cost guardrails, checkpoint/block metadata.
- `agent/bom_service.py`: BOM parsing, validation, readiness, and repair flows.
- `agent/jep_lifecycle.py`: JEP draft/review lifecycle state.
- `agent/graphs/`: graph helpers for diagram, WAF, POV, JEP, and Terraform.
- `ui/src/App.tsx`: tab shell and top-level UI state.
- `ui/src/components/ChatInterface.tsx`: chat experience and Agent 0 surface.
- `ui/src/api/client.ts`: browser API client and endpoint contracts.
- `ui/src/__tests__/`: Vitest/MSW UI coverage.
- `tests/`: Python unit, integration, prompt, and live opt-in tests.

## Architecture Map

- Backend API: `drawing_agent_server.py` exposes health, artifact, chat,
  generate, clarify, upload, BOM, POV, JEP, WAF, and Terraform endpoints.
- Static UI: the backend serves the Vite build from `ui/dist/` in production.
- Orchestrator: `agent/archie_loop.py` decides whether to answer,
  clarify, run deterministic fast paths, or delegate to specialist workflows.
- ReAct prompts include internal orchestrator self-guidance; deterministic fast
  paths skip ReAct by design and are not self-guidance failures.
- Decision Context is generated per turn, persisted to context, injected into
  skills, passed to governor evaluation, included in traces, and recorded in
  the Decision Log.
- Canonical Archie memory is assembled and enforced in `agent/archie_memory.py`,
  then refreshed after user turns, saved notes, and specialist results.
  Specialist tool arguments include `_memory_snapshot`, and final specialist
  prompts must contain `[Archie Canonical Memory]`.
- Management Summary rendering is deterministic and consolidates applied
  skills, refinements, governor/critic summary, tradeoffs, artifact refs, and
  checkpoint status.
- Governor enforcement applies deterministic rules for public ingress without
  WAF/justification, root compartment usage, missing encryption, budget
  overruns, high-risk assumptions with missing inputs, and requirement
  contradictions.
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
- Specialist skills: markdown `SKILL.md` files encode domain workflows for
  BOM, diagram, POV, JEP, WAF, Terraform, critic, QA, and review behavior.
- React UI: `App.tsx` coordinates tabs; form components call typed helpers in
  `ui/src/api/client.ts`; chat lives in `ChatInterface.tsx`.
- Tests: Python tests use `pytest.ini` markers; UI tests use Vitest,
  Testing Library, and MSW handlers.
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
pytest tests/test_orchestrator_parallel_reply.py tests/test_governor_agent.py tests/test_decision_context.py -v
pytest tests/test_bom_service.py tests/test_bom_api.py -v
pytest tests/test_terraform_api.py tests/test_terraform_graph.py -v
pytest tests/test_jep_lifecycle.py -v

# Repo gates
./scripts/test_pr_gate.sh -v
./scripts/test_nightly_prompt.sh -v
PROMPT_JUDGE_STRICT=0 ./scripts/test_nightly_prompt.sh -v

# Live opt-in only
RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_live.py -v -s
AGENT_BASE_URL=http://127.0.0.1:8080 pytest tests/test_server_live.py -v -s
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

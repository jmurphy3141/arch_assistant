# Archie — OCI Architecture Assistant

## What This Is

**Archie** is a conversational OCI solutions architect assistant. An SA describes a customer workload; Archie produces architecture diagrams, BOM pricing, POV documents, JEP documents, WAF reviews, and Terraform — in one chat session.

The project started as a single diagram-generation agent and has grown into a multi-deliverable platform. The CLAUDE.md you are reading is the authoritative description of what exists today.

---

## Architecture Overview

**Forge is the framework. Archie is the personality (system prompt + tools + hats).**

```
User (browser UI or API)
  │
  ▼
drawing_agent_server.py  ← FastAPI, port 8080, v1.9.1
  │   /api/chat/stream    → chat_stream.py → archie_session.py → Forge
  │   /upload-bom         → direct diagram pipeline
  │   /api/bom/*          → bom_service.py
  │   /health, /download
  │
  ├─ orchestrator_agent.py   Thin compatibility shim (26 lines)
  │
  ├─ archie_session.py       Thin session wrapper (~150 lines):
  │    load history + context → forge.run_turn() → save results
  │
  ├─ SkillForge (skillforge/)    Domain-agnostic ReAct orchestrator
  │    forge.py              Forge.run_turn() — the primary loop:
  │      step3_planning      → structured planning LLM call before tools
  │      requires_hat gate   → auto-activates expert hat before domain tools
  │      expert pre-action   → expert LLM call before tool dispatch
  │      tool dispatch       → calls registered tool handlers
  │      expert post-review  → quality + correctness review after tool
  │      correction loop     → injects review concern on iterate
  │    registry.py           Tool registration (ToolSpec, requires_hat)
  │    types.py              TurnResult, ToolCallRecord, TurnEvent
  │
  ├─ Archie wiring (agent/archie_wiring.py)
  │    build_forge()         Constructs Forge with Archie system prompt,
  │                          9 registered tools, and hat_engine
  │
  ├─ Tool handlers (agent/tools/)
  │    diagram.py            DiagramHandler → A2A diagram sub-agent
  │    bom.py                BomHandler → bom_service.py
  │    specialists.py        WafHandler / PovHandler / JepHandler → A2A
  │    terraform.py          TerraformHandler → sub_agents/terraform/
  │    notes.py              save_notes / get_summary / get_document
  │
  ├─ Hat system
  │    hat_engine.py              Loads agent/hats/*.md, exposes use_hat_* tools
  │    hats/diagram_for_oci.md    Expert lens: OCI diagram quality + AI/ML checks
  │    hats/oci_bom_expert.md     Expert lens: BOM service list + pricing review
  │    hats/oci_waf_reviewer.md   Expert lens: WAF findings + P1 severity
  │    hats/terraform_for_oci.md  Expert lens: Terraform correctness
  │    hats/oci_customer_pov_writer.md  Expert lens: POV document quality
  │    hats/jep_writer.md         Expert lens: JEP document quality
  │    hats/critic.md             Critic post-review lens (manual only)
  │    hats/governor.md           Guardrail lens (manual only)
  │
  ├─ Sub-agents (independent A2A services)
  │    sub_agents/bom/        BOM specialist
  │    sub_agents/diagram/    Diagram specialist
  │    sub_agents/pov/        POV specialist
  │    sub_agents/jep/        JEP specialist
  │    sub_agents/waf/        WAF specialist
  │    sub_agents/terraform/  Terraform specialist
  │
  ├─ Diagram pipeline
  │    bom_parser.py          BOM.xlsx / inline text → ServiceItem list + LLM prompt
  │    OCI GenAI (inference)  Prompt → LayoutIntent JSON
  │    intent_compiler.py     LayoutIntent → validated layout spec
  │    layout_engine.py       Spec → absolute x,y positions
  │    drawio_generator.py    Positions → flat draw.io XML
  │
  └─ Persistence
       document_store.py      Notes, docs, conversation history, Terraform bundles
       context_store.py       Per-customer working context + agent run log
       persistence_objectstore.py   OCI Object Storage adapter
       object_store_oci.py    Low-level OCI OS client
```

---

## Repository Structure

```
arch_assistant/
├── drawing_agent_server.py     # FastAPI server — single entry point (4,900 lines)
├── a2a_server.py               # A2A protocol server (port 8081)
├── mcp_server.py               # MCP stdio server
├── dev_server.py               # Local dev variant (no OCI auth required)
├── config.yaml                 # All non-secret server config
├── requirements.txt
├── Dockerfile
├── deploy/oci-agent.service    # systemd unit for production
│
├── skillforge/                 # Domain-agnostic ReAct orchestrator framework
│   ├── forge.py                # Forge class — run_turn(), reasoning loop, hat gates
│   ├── registry.py             # ToolSpec, register_tool(), requires_hat
│   ├── types.py                # TurnResult, ToolCallRecord, TurnEvent, ToolResult
│   └── __init__.py
│
├── agent/
│   ├── orchestrator_agent.py   # Thin compatibility shim for existing imports
│   ├── archie_session.py       # Thin session wrapper: load state → forge.run_turn() → save
│   ├── archie_wiring.py        # build_forge(): Archie system prompt + tool registration
│   ├── archie_memory.py        # Memory/context assembly and enforcement helpers
│   ├── hat_engine.py           # Loads hats and exposes use_hat_* tools
│   ├── safety_rules.py         # Thin deterministic safety checks
│   ├── bom_parser.py           # BOM → ServiceItem list + LLM prompt
│   ├── bom_service.py          # Live OCI pricing, BOM generation, repair loop
│   ├── bom_stub.py             # Offline stub for tests
│   ├── layout_engine.py        # LayoutIntent spec → x,y positions
│   ├── intent_compiler.py      # Validates + post-processes LLM layout output
│   ├── drawio_generator.py     # Positions → flat draw.io XML
│   ├── oci_standards.py        # OCI icon stencil data (147KB, do not edit)
│   ├── pov_agent.py            # Point-of-View document writer
│   ├── jep_agent.py            # JEP document writer
│   ├── jep_lifecycle.py        # JEP state machine
│   ├── waf_agent.py            # WAF review agent
│   ├── reference_architecture.py    # Oracle reference pattern selector
│   ├── external_corpus_scorer.py    # Diagram quality scorer vs. corpus
│   ├── context_store.py        # Per-customer working context
│   ├── document_store.py       # Notes, docs, history, Terraform bundles
│   ├── decision_context.py     # Assembles context snapshot for LLM calls
│   ├── persistence_objectstore.py   # OCI Object Storage adapter + in-memory stub
│   ├── object_store_oci.py     # Low-level OCI OS client
│   ├── llm_client.py           # Legacy OCI ADK client (kept for reference)
│   ├── llm_inference_client.py # Direct OCI GenAI inference client (active)
│   ├── runtime_config.py       # Reads config.yaml, resolves per-agent LLM config
│   ├── notifications.py        # Telegram bot integration (optional)
│   ├── layout_intent.py        # LayoutIntent dataclass + validator
│   ├── png_exporter.py         # draw.io CLI → PNG (requires CLI)
│   │
│   ├── tools/                  # Forge tool handlers (called by forge.run_turn)
│   │   ├── diagram.py          # DiagramHandler — calls diagram sub-agent A2A
│   │   ├── bom.py              # BomHandler — calls bom_service.py
│   │   ├── specialists.py      # WafHandler, PovHandler, JepHandler — A2A
│   │   ├── terraform.py        # TerraformHandler — calls terraform sub-agent
│   │   └── notes.py            # save_notes, get_summary, get_document
│   │
│   ├── hats/                   # Expert lenses (markdown) loaded by hat_engine
│   │   ├── diagram_for_oci.md       # OCI diagram quality, AI/ML completeness
│   │   ├── oci_bom_expert.md        # BOM service list and pricing review
│   │   ├── oci_waf_reviewer.md      # WAF findings, P1 severity checks
│   │   ├── terraform_for_oci.md     # Terraform correctness and best practices
│   │   ├── oci_customer_pov_writer.md  # POV document quality
│   │   ├── jep_writer.md            # JEP document quality
│   │   ├── critic.md                # Critic post-review (manual activation only)
│   │   └── governor.md              # Guardrail lens (manual activation only)
│
│   └── standards/
│       └── oracle_reference_bundle.json
│
├── sub_agents/                 # Independent A2A specialist services
│   ├── bom/
│   ├── diagram/
│   ├── pov/
│   ├── jep/
│   ├── waf/
│   └── terraform/
│
├── ui/                         # React + Vite frontend ("Archie")
│   ├── src/
│   │   ├── App.tsx             # Root — sidebar + mode routing
│   │   ├── components/
│   │   │   ├── ChatInterface.tsx     # Primary streaming chat
│   │   │   ├── BomAdvisor.tsx        # BOM advisory + XLSX export
│   │   │   ├── GenerateForm.tsx      # Direct diagram generation
│   │   │   ├── TerraformForm.tsx
│   │   │   ├── WafForm.tsx
│   │   │   ├── JepForm.tsx
│   │   │   ├── PovForm.tsx
│   │   │   ├── ArtifactPreviewPanel.tsx
│   │   │   ├── ChatSidebar.tsx
│   │   │   └── ...
│   │   ├── api/client.ts       # All backend API calls
│   │   └── agents/registry.ts  # Agent/mode registry
│   └── src/__tests__/          # Vitest unit tests
│
├── tests/                      # Backend pytest suite (40+ test files)
│   ├── scenarios/              # End-to-end scenario tests (s1/s2/s3)
│   ├── prompt_quality/         # LLM judge + recursive prompt quality tests
│   └── fixtures/outputs/       # Generated .drawio files committed by the server
│
├── server/                     # Secondary FastAPI app (OCI Object Storage service layer)
│   └── app/main.py             # Separate process; used for storage proxy
│
└── docs/                       # Design docs, requirements, migration plans
    ├── pipeline.md
    ├── orchestrator.md
    └── requirements-*.md
```

---

## Key Design Decisions

### Flat draw.io XML
Every cell is emitted at `parent="1"` (root). Icons sit visually inside subnet boxes but are **not** children. This makes every element independently draggable — never change this.

### OCI Icons
`agent/oci_standards.py` contains compressed multi-cell icon XML from `OCI_Library.xml` (Oracle draw.io stencil library v24.2). Do not edit — regenerate from source if icons need updating.

### Gateway X positioning
Layout engine overrides gateway X after computing subnet bounding boxes:
- IGW, NAT, DRG: `x = vcn_left - icon_w/2`
- SGW: `x = vcn_right - icon_w/2`

### Forge is the orchestrator — Archie is the personality
`skillforge/forge.py` owns ALL orchestration: planning, hat activation, expert
pre-action, tool dispatch, expert post-review, and correction loops.
`agent/archie_session.py` is a thin session wrapper (~150 lines) that loads
state, calls `forge.run_turn()`, and saves results. It must never contain
routing logic, LLM calls, or tool dispatch.

`agent/archie_wiring.py` builds the Forge instance with the Archie system
prompt (OCI architect persona + tool sequencing rules) and registers the 9
domain tools via `build_forge()`.

Expert lenses are markdown hats in `agent/hats/`, loaded by `hat_engine.py`.
Forge auto-activates the required hat before any domain tool call via the
`requires_hat` field on each registered tool.

### Sub-agents are A2A services
Specialists live under `sub_agents/`: BOM, diagram, POV, JEP, WAF, and
Terraform. Archie delegates to them through `agent/sub_agent_client.py`; do not
reintroduce in-process graph wrappers.

### Deterministic safety guard
`agent/safety_rules.py` holds the thin deterministic hard-block checks. Critic
and governor behavior now lives in hats, not Python modules.

### The server auto-commits diagrams to git
`config.yaml` `git_push.enabled: true` causes the production server (`opc@agent-bastion`) to commit generated `.drawio` files directly to `tests/fixtures/outputs/`. This is intentional — it enables diagram quality regression tracking. Do not disable it without understanding the test impact.

---

## Auth & Config

**OCI Instance Principal** — the server runs on OCI Compute. No `~/.oci/config`. Never hardcode credentials.

**OCI Identity Domain OAuth** — the web UI uses OIDC for user sessions. Config via environment variables (see `.env.example`).

All non-secret config lives in `config.yaml` (OCI resource OCIDs, inference endpoint, region, agent tuning). These are not secrets.

Active region: **us-chicago-1** (not us-phoenix-1 — that is stale in some old comments).

---

## Development Commands

### Run server locally (requires OCI auth)
```bash
python3.11 -m uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 --reload
```

### Run tests
```bash
pytest tests/ -v
# Skip live OCI tests:
pytest tests/ -v -m "not live"
```

### Build the UI
```bash
cd ui && npm install && npm run build
```

### Deploy to OCI Compute
```bash
# Update code on server
git push origin main
ssh opc@10.0.3.47 '
  cd ~/drawing-agent &&
  git pull origin main &&
  find . -name "*.pyc" -delete &&
  cd ui && npm install && npm run build && cd .. &&
  pkill -f uvicorn;
  nohup python3.11 -m uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 > agent.log 2>&1 &
  sleep 3 && curl -s http://localhost:8080/health
'
```

### API smoke tests
```bash
# Health
curl -s http://10.0.3.47:8080/health

# Chat (primary path)
curl -X POST http://10.0.3.47:8080/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello", "customer_id": "test1"}'

# Direct diagram from BOM file
curl -X POST http://10.0.3.47:8080/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1"
```

---

## Known Debt — Do Not Make Worse

1. **Keep `orchestrator_agent.py` thin.** New work belongs in `archie_session.py`
   (session management) or `skillforge/forge.py` (orchestration), not in the
   compatibility shim.

2. **`server/` directory** is a secondary FastAPI app for OCI Object Storage
   proxying. It is a separate process, not part of the main server startup. Do
   not merge its routes into `drawing_agent_server.py`.

3. **`archie_session.py` is a thin session wrapper.** It must not contain
   routing logic, LLM calls outside `forge.run_turn()`, or tool dispatch.
   All orchestration belongs in `skillforge/forge.py`. All sequencing rules
   belong in the Archie system prompt in `agent/archie_wiring.py`. Any code
   added to `archie_session.py` that bypasses `forge.run_turn()` silently
   kills the p39–p43 expert reasoning for that request type.
   `tests/test_archie_forge_wiring.py` will catch regressions.

---

## OCI Environment

| Setting | Value |
|---------|-------|
| Host | `opc@10.0.3.47` |
| Port | 8080 |
| App dir | `~/drawing-agent/` |
| Python | `python3.11` (OCI ADK incompatible with 3.9) |
| Region | `us-chicago-1` |
| Auth | Instance Principal |
| Object Storage bucket | `agent_assistante` (namespace: `oraclejamescalise`) |
| Git auto-push | enabled — server commits generated diagrams to `tests/fixtures/outputs/` |

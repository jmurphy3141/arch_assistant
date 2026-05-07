# Archie — OCI Architecture Assistant

## What This Is

**Archie** is a conversational OCI solutions architect assistant. An SA describes a customer workload; Archie produces architecture diagrams, BOM pricing, POV documents, JEP documents, WAF reviews, and Terraform — in one chat session.

The project started as a single diagram-generation agent and has grown into a multi-deliverable platform. The CLAUDE.md you are reading is the authoritative description of what exists today.

---

## Architecture Overview

```
User (browser UI or API)
  │
  ▼
drawing_agent_server.py  ← FastAPI, port 8080, v1.9.1
  │   /api/chat           → archie_loop.py via orchestrator_agent.py shim
  │   /upload-bom         → direct diagram pipeline
  │   /api/bom/*          → bom_service.py
  │   /api/terraform/*    → jep/pov/waf/terraform agents
  │   /health, /download
  │
  ├─ orchestrator_agent.py   26-line compatibility shim for existing imports
  ├─ archie_loop.py          ReAct loop; dispatches internal tools:
  │    generate_diagram       → diagram pipeline (A2A self-call)
  │    generate_bom           → bom_service.py
  │    generate_pov           → pov_agent.py
  │    generate_jep           → jep_agent.py
  │    generate_waf           → waf_agent.py
  │    generate_terraform     → sub_agents/terraform/
  │    save_notes / get_summary / get_document
  │
  ├─ Hat system
  │    hat_engine.py          Loads agent/hats/*.md and exposes use_hat_* tools
  │    hats/critic.md         Critic lens for reviewing specialist output
  │    hats/governor.md       Guardrail lens for cost/security/quality review
  │
  ├─ Sub-agents
  │    sub_agents/bom/        BOM specialist A2A service
  │    sub_agents/diagram/    Diagram specialist A2A service
  │    sub_agents/pov/        POV specialist A2A service
  │    sub_agents/jep/        JEP specialist A2A service
  │    sub_agents/waf/        WAF specialist A2A service
  │    sub_agents/terraform/  Terraform specialist A2A service
  │
  ├─ Diagram pipeline
  │    bom_parser.py          BOM.xlsx / inline text → ServiceItem list + LLM prompt
  │    OCI GenAI (inference)  Prompt → LayoutIntent JSON
  │    intent_compiler.py     LayoutIntent → validated layout spec
  │    layout_engine.py       Spec → absolute x,y positions
  │    drawio_generator.py    Positions → flat draw.io XML
  │
  ├─ Reference architecture
  │    reference_architecture.py     Selects Oracle reference patterns
  │    external_corpus_scorer.py     Scores diagrams against corpus
  │    standards/oracle_reference_bundle.json
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
├── agent/
│   ├── orchestrator_agent.py   # Thin compatibility shim for Agent 0 imports
│   ├── archie_loop.py          # Agent 0 ReAct loop, routing, and tool dispatch
│   ├── archie_memory.py        # Memory/context assembly and enforcement helpers
│   ├── hat_engine.py           # Loads hats and exposes hat activation tools
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
│   ├── diagram_waf_orchestrator.py  # Diagram + WAF combined loop
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
│   ├── diagram_orchestrator.py # DEPRECATED — do not add code here
│   │
│   ├── hats/                   # Archie expert lenses selected as tools
│   │   ├── critic.md, governor.md, diagram_builder.md, bom_reviewer.md
│   │   └── terraform_reviewer.md, waf_reviewer.md
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

### Archie loop and hats
`agent/orchestrator_agent.py` is only a compatibility shim. The real Agent 0
implementation is `agent/archie_loop.py`; memory/context helpers live in
`agent/archie_memory.py`. Expert lenses are markdown hats in `agent/hats/`,
loaded by `agent/hat_engine.py` and selected by Archie via `use_hat_*` tools.

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

1. **Keep `orchestrator_agent.py` thin.** New Agent 0 work belongs in
   `archie_loop.py` or a focused helper module, not in the compatibility shim.

2. **`diagram_orchestrator.py` is deprecated.** It has a `DeprecationWarning` at
   the top. Do not add code to it. It should be deleted once confirmed unused.

3. **`server/` directory** is a secondary FastAPI app for OCI Object Storage
   proxying. It is a separate process, not part of the main server startup. Do
   not merge its routes into `drawing_agent_server.py`.

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

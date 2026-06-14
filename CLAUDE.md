# Archie — OCI Architecture Assistant

## What This Is

**Archie** is a conversational OCI solutions architect and Sales Engineer second brain. An SE describes a customer workload; Archie produces architecture diagrams, BOM pricing, POV documents, JEP documents, WAF reviews, and Terraform — while simultaneously tracking the deal's human layer: stakeholders, objections, commitments, competitive posture, and C3E phase.

The project started as a single diagram-generation agent and has grown into a multi-deliverable platform with a full relationship/sales memory layer.

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
  │   /api/briefing/*     → briefing router (engagement state, meeting prep, SE accounts)
  │   /api/notes/upload   → debrief extraction + pending_debrief checkpoint
  │   /health, /download
  │
  ├─ orchestrator_agent.py   Thin compatibility shim (26 lines)
  │
  ├─ archie_session.py       Thin session wrapper (~150 lines):
  │    load history + context → forge.run_turn() → save results
  │    upserts SE engagement index after each turn
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
  │                          10 registered tools (incl. confirm_debrief), and hat_engine
  │
  ├─ Tool handlers (agent/tools/)
  │    diagram.py            DiagramHandler → A2A diagram sub-agent
  │    bom.py                BomHandler → bom_service.py
  │    specialists.py        WafHandler / PovHandler / JepHandler → A2A
  │    terraform.py          TerraformHandler → sub_agents/terraform/
  │    notes.py              save_notes / get_summary / get_document / confirm_debrief
  │
  ├─ Second brain layer
  │    engagement_mission.py      C3E phase tracker + next-step proactivity
  │    meeting_prep.py            Deterministic meeting prep assembler
  │    server/routes/briefing.py  REST endpoints: engagement, SE accounts, prep
  │    agent/archie_memory.py     extract_relationship_facts_llm() + regex fallback
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
│   ├── archie_memory.py        # Memory/context assembly, extract_relationship_facts_llm()
│   ├── hat_engine.py           # Loads hats and exposes use_hat_* tools
│   ├── engagement_mission.py   # C3E phase tracker, next-step proactivity (suggest_next_step)
│   ├── meeting_prep.py         # build_meeting_prep() — deterministic prep assembler
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
│   ├── context_store.py        # Per-customer working context + relationship schema
│   ├── document_store.py       # Notes, docs, history, Terraform bundles, SE index
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
│   │   └── notes.py            # save_notes, get_summary, get_document, confirm_debrief
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
├── server/
│   └── routes/
│       ├── documents.py        # /api/notes/upload — debrief extraction + pending_debrief
│       └── briefing.py         # /api/briefing/* — engagement state, SE accounts, meeting prep
│
├── ui/                         # React + Vite frontend ("Archie")
│   ├── src/
│   │   ├── App.tsx             # Root — dark-ops sidebar + mode routing + thinking glow
│   │   ├── index.css           # JARVIS dark-ops theme: --accent #00e5ff, scan-line overlay
│   │   ├── components/
│   │   │   ├── ChatInterface.tsx     # Primary streaming chat
│   │   │   ├── MeetingBriefing.tsx   # Briefing tab: engagement state + SeAccountsPanel
│   │   │   ├── EngagementMemoryPanel.tsx  # Per-engagement relationship + artifact view
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
└── docs/                       # Design docs, requirements, migration plans
    ├── pipeline.md
    ├── orchestrator.md
    └── requirements-*.md
```

---

## Second Brain — Relationship & Sales Memory Layer

Archie tracks the human side of every deal alongside the technical artifacts.

### Relationship Schema (`agent/context_store.py`)
`archie.relationship` in every customer context:
```python
{
    "stakeholders": [],   # [{name, role, disposition, notes}]
                          # disposition: champion|economic_buyer|blocker|influencer|unknown
    "objections":   [],   # [{raised_by, concern, status, response}]  status: open|addressed
    "commitments":  [],   # [{who, what, due, status}]  who: oracle|customer
    "competitive":  {},   # {incumbent, competitors:[], their_claim, our_counter}
    "action_items": [],   # [{owner, task, due, status, created}]  status: open|done
    "meetings":     [],   # [{date, attendees:[], note_key, summary}]
}
```
`merge_archie_relationship_facts(context, facts)` — list fields append-with-dedup; competitive deep-merges. When a stakeholder with `disposition="economic_buyer"` is merged, it also writes through to `client_facts.economic_buyer` so the mission blocker clears automatically.

### Debrief Loop (`server/routes/documents.py`)
1. SE uploads meeting notes → `/api/notes/upload`
2. Server runs `extract_relationship_facts_llm()` (LLM-first; regex fallback on failure)
3. Stores structured `pending_debrief` checkpoint in context
4. Returns debrief preview in upload response; UI shows confirmation prompt
5. SE confirms in chat → `confirm_debrief` tool → `merge_archie_relationship_facts`

`extract_relationship_facts_llm(text, run_fn)` in `agent/archie_memory.py`: structured JSON prompt requesting stakeholders / objections / commitments / action_items / competitive; strips markdown fences, validates types, returns `{}` on any failure so the caller falls back to regex.

### C3E Phase Tracking (`agent/engagement_mission.py`)
Correct phase order:
```
Qualify → Discover → Develop → Design → Prove → Win → Deploy → Support → Grow
```
`_init_mission()` sets current phase to the **first incomplete** phase (gated by `PHASE_ARTIFACTS`). `record_milestone()` auto-advances phase when all required artifacts for the current phase are done.

Proactivity priority in `suggest_next_step()` (called after every turn when no generation tool fired):
1. Overdue oracle commitment (dateutil parse of stored `due` field)
2. Unaddressed objection at Prove / Win / Deploy phase
3. Next required artifact offer

Same nudge is suppressed for 3 consecutive turns via `archie._last_next_step_offer`.

### Meeting Prep (`agent/meeting_prep.py`)
`build_meeting_prep(context) -> str` assembles:
- Mission blockers (`economic_buyer unknown`, `current_platform unknown`)
- Unanswered discovery fields
- Open objections with originator
- Pending commitments with due date
- Lessons learned on this engagement (grouped by tool)

Exposed via `GET /api/briefing/{customer_id}/prep` and as a chat hat (`agent/hats/meeting_prep.md`).

### SE Cross-Account Dashboard
`GET /api/se/{se_id}/accounts` reads `se/{se_id}/engagements.json` — a per-SE index upserted after every turn with `{phase, blockers, next_required, completed_artifacts, last_activity}` for every customer. `SeAccountsPanel` in `MeetingBriefing.tsx` renders a cross-account table: customer | phase | blockers | what's due.

When no customer is selected in the Briefing tab, the SE sees all their accounts.

---

## UI — JARVIS Dark-Ops Aesthetic

Design principle: **calm at rest, glow when active.** The UI has zero decoration when Archie is idle; glow is earned by system activity.

### CSS Variables (`ui/src/index.css`)
```css
--accent:  #00e5ff                        /* cyan */
--accentG: rgba(0, 229, 255, 0.08)        /* tinted background */
--glow:    0 0 8px rgba(0,229,255,0.45), 0 0 20px rgba(0,229,255,0.15)
```

Scan-line overlay via `body::after` (`repeating-linear-gradient` at 4px pitch, opacity ~1.2%).

### Activity Glow Classes
| Class | Trigger | Effect |
|-------|---------|--------|
| `.rail-thinking` | `thinkingStatus !== null` (App.tsx) | Right-rail pulses via `rail-pulse` keyframe |
| `.hat-glow-in` | Hat badges in EngagementMemoryPanel | Cyan bloom on mount |
| `.debrief-wipe-in` | Debrief panel reveal | Slide + fade wipe |
| `.tool-dot-fire` | ToolChip dot in ChatInterface | One-shot cyan flash |

### Color tokens (replacing orange `#e8571a`)
All interactive elements use `--accent` (`#00e5ff`). Disposition pills: champion/economic_buyer = green `#2ecc8a`; blocker = red `#e8415a`.

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
prompt (OCI architect persona + tool sequencing rules) and registers the 10
domain tools via `build_forge()`.

Expert lenses are markdown hats in `agent/hats/`, loaded by `hat_engine.py`.
Forge auto-activates the required hat before any domain tool call via the
`requires_hat` field on each registered tool.

### Second brain is Archie-domain only — Forge is untouched
All relationship memory, debrief loop, and proactivity logic lives in
`agent/`, `server/routes/`, and `ui/src/components/`. The Forge framework
(`skillforge/`) has zero knowledge of C3E phases, stakeholders, or engagement
state. Never add deal-tracking logic to Forge.

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

# Engagement briefing
curl -s http://10.0.3.47:8080/api/briefing/test1

# SE cross-account view
curl -s http://10.0.3.47:8080/api/se/default/accounts

# Meeting prep
curl -s http://10.0.3.47:8080/api/briefing/test1/prep

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

4. **Relationship extraction is LLM-first with regex fallback.** Never swap
   this order or make LLM extraction blocking — if the inference call fails,
   the debrief still lands via the regex pass, and the SE can confirm before
   anything is persisted.

5. **`suggest_next_step` offers, never fires.** It returns a string; the caller
   appends it to the turn reply. It must never call a generation tool directly.
   The 3-turn suppression (`archie._last_next_step_offer`) prevents nagging.

---

## Open Enhancements (Next Up)

- **Inject relationship context into specialists** — open objections, economic
  buyer, and competitive posture should flow into `_build_archie_specialist_context()`
  so POV / BOM / WAF / JEP generators are aware of deal dynamics.
- **"Due this week" filter** in `SeAccountsPanel` — highlight commitments and
  action items with `due` within 7 days across all accounts.
- **Auto-load prep on customer select** — remove the button click in
  `MeetingBriefing.tsx`; load the prep brief automatically when a customer row
  is opened.

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

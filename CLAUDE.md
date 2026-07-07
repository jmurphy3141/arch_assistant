# Archie — OCI Architecture Assistant

## What This Is

**Archie** is a conversational OCI solutions architect and Sales Engineer second brain. An SE describes a customer workload; Archie produces architecture diagrams, BOM pricing, POV documents, JEP documents, WAF reviews, and Terraform — while simultaneously tracking the deal's human layer: stakeholders, objections, commitments, competitive posture, and C3E phase.

The project started as a single diagram-generation agent and has grown into a multi-deliverable platform with a full relationship/sales memory layer.

---

## Architecture Overview

**Two orchestration paths, one flag.** `config.yaml` → `orchestrator.agent_mode`
selects `native` (default, switched from forge for live testing) or `forge`
(explicit compatibility path). Native is a leaner native-tool-calling loop
with no hats (Decision #8) and its own retrieval/compute/export tool surface;
forge is the hat-gated ReAct ceremony described below. Both paths share the
same tool handlers, sub-agents, and second-brain layer underneath. Archie is
the personality (system prompt + tools [+ hats, in forge mode]) on top of
either loop.

```
User (browser UI or API)
  │
  ▼
drawing_agent_server.py  ← FastAPI, port 8080, AGENT_VERSION="1.9.1" (const; codebase is well past it — see CHANGELOG.md)
  │   /api/chat/stream    → chat_stream.py → archie_session.py → agent_mode dispatch
  │   /api/chat/background→ server/routes/chat.py → agent_mode dispatch (native or forge)
  │   /upload-bom         → direct diagram pipeline
  │   /api/bom/*          → bom_service.py
  │   /api/briefing/*     → briefing router (engagement state, meeting prep, SE accounts)
  │   /api/notes/upload   → debrief extraction + pending_debrief checkpoint
  │   /health, /download
  │
  ├─ orchestrator_agent.py   Thin compatibility shim (26 lines)
  │
  ├─ archie_session.py       Thin session wrapper (~150 lines):
  │    get_agent_mode() reads config.yaml → routes to forge.run_turn()
  │    or archie_native_loop.run_turn(); saves results either way;
  │    upserts SE engagement index after each turn
  │
  ├─ FORGE PATH (agent_mode: forge — explicit compatibility path)
  │    SkillForge (skillforge/)    Domain-agnostic ReAct orchestrator
  │      forge.py              Forge.run_turn() — the primary loop:
  │        step3_planning      → structured planning LLM call before tools
  │        requires_hat gate   → auto-activates expert hat before domain tools
  │        expert pre-action   → expert LLM call before tool dispatch
  │        tool dispatch       → calls registered tool handlers
  │        expert post-review  → quality + correctness review after tool
  │        correction loop     → injects review concern on iterate
  │      registry.py           Tool registration (ToolSpec, requires_hat)
  │      types.py              TurnResult, ToolCallRecord, TurnEvent
  │    Archie wiring (agent/archie_wiring.py)
  │      build_forge()         Constructs Forge with Archie system prompt,
  │                            16 registered domain tools, and hat_engine
  │    Hat system (forge-only — retired from native mode, Decision #8)
  │      hat_engine.py         Loads agent/hats/*.md, exposes use_hat_* tools
  │      hats/*.md             19 expert lenses — see "Hat System" below
  │
  ├─ NATIVE PATH (agent_mode: native — default)
  │    archie_native_loop.py  Native tool-calling ReAct loop, no hats:
  │      per-tool error isolation (a failing tool → status="error" ToolResult,
  │      turn continues); reasoning_sink/notify hooks emit live thinking +
  │      tool-chip events; complete artifact-type map for all 12 generate tools
  │    archie_memory_retrieval.py  recall_fact / search_notes / get_decisions /
  │                            list_artifacts / get_meeting_summaries
  │    reference_tools.py     lookup_compute_shapes / lookup_price /
  │                            lookup_reference_architecture (grounding by retrieval)
  │    file_reader_tools.py   read_file_content — read any stored doc/spreadsheet
  │    compute_tools.py       compute — deterministic cost/TCO/proration math
  │    export_tools.py        export_artifact — diagram→PNG, spreadsheet→CSV
  │    semantic_notes.py      semantic_search — meaning/paraphrase retrieval over
  │                            one engagement's transcript index (isolated;
  │                            disambiguated from keyword search_notes)
  │
  ├─ Tool handlers (agent/tools/) — shared by both paths
  │    diagram.py            DiagramHandler → A2A diagram sub-agent
  │    bom.py                BomHandler → bom_service.py
  │    specialists.py        WafHandler / PovHandler / JepHandler / etc. → A2A
  │    terraform.py          TerraformHandler → sub_agents/terraform/
  │    notes.py              save_notes / get_summary / get_document / confirm_debrief
  │
  ├─ Second brain layer
  │    engagement_mission.py      C3E phase tracker + next-step proactivity
  │    meeting_prep.py            Deterministic meeting prep assembler
  │    server/routes/briefing.py  REST endpoints: engagement, SE accounts, prep
  │    agent/archie_memory.py     extract_relationship_facts_llm() + regex fallback
  │    transcript_ingest.py       Distill (same debrief path) + chunk/embed/index
  │                            a meeting transcript, isolated per engagement
  │    embedding_client.py        OCI GenAI embeddings, swappable embed_fn
  │
  ├─ Sub-agents (independent A2A services) — see "Sub-Agents" below for all 12
  │    sub_agents/bom/        sub_agents/diagram/     sub_agents/pov/
  │    sub_agents/jep/        sub_agents/waf/         sub_agents/terraform/
  │    sub_agents/tech_research/  sub_agents/poc_strategist/
  │    sub_agents/presentation/   sub_agents/sales_deck/
  │    sub_agents/sta/            sub_agents/technical_proposal/
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
│   ├── archie_session.py       # Thin session wrapper: get_agent_mode() → forge or native → save
│   ├── archie_wiring.py        # build_forge(): Archie system prompt + tool registration
│   ├── archie_native_loop.py   # Native tool-calling loop (agent_mode: native) — no hats
│   ├── archie_memory.py        # Memory/context assembly, extract_relationship_facts_llm()
│   ├── archie_memory_impl.py   # MemorySnapshot assembly/update implementation
│   ├── archie_memory_retrieval.py  # Native-only: recall_fact/search_notes/get_decisions/
│   │                            #   list_artifacts/get_meeting_summaries tool specs
│   ├── reference_tools.py      # Native-only: lookup_compute_shapes/lookup_price/
│   │                            #   lookup_reference_architecture tool specs
│   ├── file_reader_tools.py    # Native-only: read_file_content tool spec
│   ├── compute_tools.py        # Native-only: compute (deterministic math) tool spec
│   ├── export_tools.py         # Native-only: export_artifact (PNG/CSV) tool spec
│   ├── semantic_notes.py       # Native-only: semantic_search tool spec — cosine-ranks
│   │                            #   one engagement's transcript index (see transcript_ingest.py)
│   ├── transcript_ingest.py    # Distill (debrief path) + chunk/embed/store a transcript,
│   │                            #   isolated per engagement; raw text never reaches producers
│   ├── embedding_client.py     # OCI GenAI embeddings; injectable embed_fn for tests
│   ├── chat_stream.py          # SSE streaming: reasoning_sink + notification_sink wiring
│   ├── context_enricher.py     # Parallel pre-turn context enrichment before forge.run_turn
│   ├── hat_engine.py           # Loads hats and exposes use_hat_* tools (forge path only)
│   ├── engagement_mission.py   # C3E phase tracker, next-step proactivity (suggest_next_step)
│   ├── meeting_prep.py         # build_meeting_prep() — deterministic prep assembler
│   ├── note_extractor.py       # PDF/DOCX/text extraction for uploaded notes
│   ├── lesson_store.py         # Per-engagement lessons-learned store (grouped by tool)
│   ├── safety_rules.py         # Thin deterministic safety checks
│   ├── consistency_contract.py # Canonical selected-POC/BOM/Diagram identity + parity checks
│   ├── poc_composer.py         # Grounded POC brief extraction + 3-option composition
│   ├── jep_composer.py         # Grounded JEP brief extraction + canonical Markdown composition
│   ├── jep_docx_renderer.py    # JEP canonical Markdown → DOCX
│   ├── bom_parser.py           # BOM → ServiceItem list + LLM prompt
│   ├── bom_service.py          # Live OCI pricing, BOM generation, repair loop
│   ├── bom_stub.py             # Offline stub for tests
│   ├── layout_engine.py        # LayoutIntent spec → x,y positions
│   ├── intent_compiler.py      # Validates + post-processes LLM layout output
│   ├── drawio_generator.py     # Positions → flat draw.io XML
│   ├── drawio_inspector.py     # Programmatic .drawio XML inspection (tests/tools)
│   ├── diagram_waf_orchestrator.py  # Combined diagram+WAF parallel dispatch
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
│   │   ├── specialists.py      # WafHandler, PovHandler, JepHandler, etc. — A2A
│   │   ├── terraform.py        # TerraformHandler — calls terraform sub-agent
│   │   └── notes.py            # save_notes, get_summary, get_document, confirm_debrief
│   │                            #   (confirm_debrief also persists transcript-sourced
│   │                            #   client_facts/decisions when pending.source_type=="transcript")
│   │
│   ├── hats/                   # Expert lenses (markdown) loaded by hat_engine — forge path
│   │   │                       #   only; retired from agent_mode: native (Decision #8).
│   │   │                       #   19 hats total — see "Hat System" below for the full list.
│   │   ├── diagram_for_oci.md       # OCI diagram quality, AI/ML completeness
│   │   ├── oci_bom_expert.md        # BOM service list and pricing review
│   │   ├── oci_waf_reviewer.md      # WAF findings, P1 severity checks
│   │   ├── terraform_for_oci.md     # Terraform correctness and best practices
│   │   ├── oci_customer_pov_writer.md  # POV document quality
│   │   ├── jep_writer.md            # JEP document quality
│   │   ├── critic.md                # Critic post-review (manual activation only)
│   │   ├── governor.md              # Guardrail lens (manual activation only)
│   │   └── ...                      # 11 more — architecture_reviewer, c3e_navigator,
│   │                                #   deal_coach, discovery, industry_expert,
│   │                                #   infra_tech_research, meeting_prep, oci_poc_strategist,
│   │                                #   oci_presentation_writer, oci_sales_deck, sta_writer,
│   │                                #   technical_proposal_writer
│
│   └── standards/               # Reference corpus wired into producers + hats
│       ├── oracle_reference_bundle.json
│       ├── oci_compute_shapes.json         oci_shape_region_availability.json
│       ├── oci_provisioning_times.json     oci_service_slas.json
│       ├── oci_terraform_resources.json    oracle_customer_case_studies.json
│       ├── pci_dss_v4.json  hipaa_security_rule.json  fedramp_moderate_controls.json
│       └── templates/
│
├── sub_agents/                 # Independent A2A specialist services (12 total)
│   ├── bom/                    # Priced OCI Bills of Materials
│   ├── diagram/                # OCI architecture draw.io diagrams
│   ├── pov/                    # Point-of-View documents
│   ├── jep/                    # Joint Engagement Plan documents
│   ├── waf/                    # Well-Architected Framework reviews
│   ├── terraform/              # Terraform modules
│   ├── tech_research/          # Infrastructure technology research and evaluation
│   ├── poc_strategist/         # 3-option POC exploration and ranking
│   ├── presentation/           # Client-ready PowerPoint (OCI icon stencils)
│   ├── sales_deck/             # Sales enablement deck
│   ├── sta/                    # Strategic Technical Approach documents
│   └── technical_proposal/     # Technical Proposal documents
│
├── server/
│   └── routes/
│       ├── documents.py        # /api/notes/upload — debrief extraction + pending_debrief
│       ├── briefing.py         # /api/briefing/* — engagement state, SE accounts, meeting prep
│       ├── chat.py             # /api/chat, /api/chat/stream, /api/chat/background
│       ├── bom.py              # /api/bom/* — BOM advisory/generation REST surface
│       └── a2a.py              # A2A protocol route glue
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

### Transcript Memory (`agent/transcript_ingest.py`, `agent/semantic_notes.py`, native-only)
`POST /api/notes/upload` with `is_transcript=true` routes through `transcript_ingest.ingest_transcript()` instead of the generic note path:
1. **Distill** — reuses the SAME `extract_relationship_facts_llm()`/regex-fallback path as regular notes, plus decision extraction; every extracted fact/decision carries a transcript citation (meeting id + line/offset range) and an ASR `low_confidence` flag (regex-detected `inaudible`/`unclear`/`[?]` near the source line). Staged to `pending_debrief` only — **nothing touches `archie.relationship` until `confirm_debrief` runs**, identical to the regular debrief loop's confirm gate.
2. **Index (retrieval/citation only, never fed to producers)** — the raw transcript is chunked, embedded (`agent/embedding_client.py`, OCI GenAI `cohere.embed-english-v3.0` via `config.yaml` → `embedding:`, Instance Principal auth), and stored as one JSON index per engagement (`customers/{engagement_id}/transcripts/index.json`). Raw transcript bytes are stored separately from the generic notes manifest (`transcript_ingest.store_raw_transcript()`), so `read_file_content`/`get_document` and specialist prompt-building never see them — only the confirmed, distilled facts do.
3. **`semantic_search`** (native-only tool, `agent/semantic_notes.py`) cosine-ranks that one engagement's chunks for a paraphrased/conceptual query and returns cited passages ("per the `<date>` call: ..."). Its engagement id is bound at tool-construction time in `archie_native_loop.py` (closed over the per-turn `customer_id`), not passed as a queryable argument — cross-engagement leakage would require actively wrong wiring, not just a missed filter. Disambiguated from keyword `search_notes`: semantic = meaning/paraphrase match, keyword = exact term match.

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

## Hat System (forge path only)

`agent/hat_engine.py` loads every `agent/hats/*.md` file and exposes
`use_hat_*` tools; Forge auto-activates the `requires_hat` a registered tool
declares before dispatching it. Native mode (`agent_mode: native`) does not
use hats at all — Decision #8 moved that expertise into the model, the
sub-agent specialists, and the `reference_tools.py` lookup surface instead.

19 hats total:

| Hat file | Display name | Activates on |
|---|---|---|
| `diagram_for_oci.md` | OCI Diagram Architect | diagram / architecture drawing / topology / network map requests |
| `oci_bom_expert.md` | OCI BOM Expert | cost, pricing, BOM, XLSX, budget, SKU questions |
| `oci_waf_reviewer.md` | OCI WAF Reviewer | WAF / Well-Architected / security assessment requests |
| `terraform_for_oci.md` | OCI Terraform Expert | Terraform, IaC, HCL, infrastructure-as-code requests |
| `oci_customer_pov_writer.md` | OCI POV Writer | POV / Point-of-View / customer vision document requests |
| `jep_writer.md` | JEP Writer | JEP / Joint Execution Plan / POC plan document requests |
| `oci_poc_strategist.md` | OCI POC Strategist | "what POC should we build for this customer" |
| `oci_presentation_writer.md` | OCI Presentation Writer | PowerPoint / deck / slides / POC kit requests |
| `oci_sales_deck.md` | OCI Sales Deck Builder | sales deck / presentation requests |
| `sta_writer.md` | Strategic Technical Approach Writer | STA document requests |
| `technical_proposal_writer.md` | Technical Proposal Writer | Technical Proposal document requests |
| `infra_tech_research.md` | OCI Infrastructure Research Analyst | "what OCI service is best for this workload" |
| `architecture_reviewer.md` | Architecture Reviewer | architecture discussed conversationally, no formal review requested |
| `industry_expert.md` | Industry Expert | industry identified as FSI/banking/insurance/capital markets |
| `discovery.md` | Discovery Conductor | customer being described for the first time |
| `deal_coach.md` | Deal Coach | competitive situation / "why would they choose OCI" questions |
| `c3e_navigator.md` | C3E Navigator | SE asks about C3E phase or engagement process |
| `meeting_prep.md` | (meeting prep) | pre-call brief / debrief requests — see Second Brain section |
| `critic.md` | Critic | post-review of a `critique_enabled` tool result (manual activation only) |
| `governor.md` | Governor | BOM/Terraform/WAF output finalization guardrail (manual activation only) |

Hat frontmatter (`hat_rules.when_to_activate`, `can_hand_off_to`,
`suggested_next_hat`, `resume_condition`, `memory_focus.priority_fields`)
drives auto-activation and cross-hat handoff — see any hat file for the exact
schema.

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
`skillforge/forge.py` owns ALL orchestration on the forge path: planning, hat
activation, expert pre-action, tool dispatch, expert post-review, and
correction loops. `agent/archie_session.py` is a thin session wrapper (~150
lines) that loads state, dispatches on `get_agent_mode()`, calls
`forge.run_turn()` or `archie_native_loop.run_turn()`, and saves results. It
must never contain routing logic, LLM calls, or tool dispatch itself.

`agent/archie_wiring.py` builds the Forge instance with the Archie system
prompt (OCI architect persona + tool sequencing rules) and registers 16
domain tools via `build_forge()`.

Expert lenses are markdown hats in `agent/hats/`, loaded by `hat_engine.py`.
Forge auto-activates the required hat before any domain tool call via the
`requires_hat` field on each registered tool.

### Native mode drops hats; forge mode is untouched (Decision #8)
`config.yaml` → `orchestrator.agent_mode` defaults to `"native"` (switched
from `"forge"` for live testing). Native mode routes turns through
`agent/archie_native_loop.py`: native tool-calling, no hat gate, no expert
pre-action/post-review ceremony — the
model reasons directly, grounding itself via `reference_tools.py` /
`archie_memory_retrieval.py` / `file_reader_tools.py` /`compute_tools.py` /
`export_tools.py` instead of a markdown expert lens. Per-tool failures are
isolated (one bad tool call → `status="error"` ToolResult, turn continues)
and `reasoning_sink`/`notify()` hooks drive the same live thinking/tool-chip
UI events the forge path already used. The forge path's behavior must stay
byte-for-byte unchanged regardless of native-loop changes.

### Second brain is Archie-domain only — Forge is untouched
All relationship memory, debrief loop, and proactivity logic lives in
`agent/`, `server/routes/`, and `ui/src/components/`. The Forge framework
(`skillforge/`) has zero knowledge of C3E phases, stakeholders, or engagement
state. Never add deal-tracking logic to Forge. This applies equally to the
native loop — it is Archie-domain code, not part of `skillforge/`.

### Sub-agents are A2A services
Specialists live under `sub_agents/` — 12 total: BOM, diagram, POV, JEP, WAF,
Terraform, tech research, POC strategist, presentation, sales deck, STA, and
technical proposal. Archie delegates to them through
`agent/sub_agent_client.py`; do not reintroduce in-process graph wrappers.

### Deterministic safety guard
`agent/safety_rules.py` holds the thin deterministic hard-block checks. Critic
and governor behavior now lives in hats, not Python modules.

### The server auto-commits diagrams to git
`config.yaml` `git_push.enabled: true` causes the production server (`opc@agent-bastion`) to commit generated `.drawio` files directly to `tests/fixtures/outputs/`. This is intentional — it enables diagram quality regression tracking. Do not disable it without understanding the test impact.

---

## Auth & Config

**OCI Instance Principal** — the server runs on OCI Compute. No `~/.oci/config`. Never hardcode credentials.

**OCI Identity Domain OAuth** — the web UI uses OIDC for user sessions. Config via environment variables (see `.env.example`).

All non-secret config lives in `config.yaml` (OCI resource OCIDs, inference endpoint, region, agent tuning, `embedding:` model/endpoint for transcript semantic retrieval). These are not secrets.

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

Done since this list was last written: relationship context (open objections,
economic buyer, competitive posture) already flows into the specialist
prompt-context builder in `agent/archie_wiring.py` — POV/BOM/WAF/JEP
generators see deal dynamics.

Still open, UI-only:
- **"Due this week" filter** in `SeAccountsPanel` — highlight commitments and
  action items with `due` within 7 days across all accounts.
- **Auto-load prep on customer select** — remove the button click in
  `MeetingBriefing.tsx`; load the prep brief automatically when a customer row
  is opened.

### Phase status (see `PLAN.md` for the full plan)
The p0-p82 task sequence is now fully landed. Phases 5-7's *enabler* tasks are
done; each phase's "then" follow-on work (listed below) has not been spec'd
into task files yet.

- **Phase 5 (Native Agent Loop):** complete — native mode has live streaming
  events, per-tool error isolation, full artifact-type coverage, and
  `agent_mode`-aware background chat. `config.yaml` now defaults to
  `"native"` for live testing (switched from `"forge"`); forge remains
  available as an explicit compatibility path.
- **Phase 6 (Better Sub-Agents):** complete. `tasks/p78-subagent-quality-harness.md`
  and `tasks/p80-baseline-integrity.md` are both `done` —
  `docs/subagent-quality.json` now reflects a REAL baseline
  (`environment.terraform_cli_available: true`, `producer_runs: 3`,
  `judge_runs: 3`, repo-relative golden paths). Current worst-to-best ranking:
  JEP 1.29 / Diagram 2.40 / Terraform 2.42 / BOM 3.27 / POV 4.04 / WAF 4.98.
  Notable finding from the real run: Terraform's `terraform_validate` objective
  check passed **0 of 3** producer runs — the prior baseline couldn't see this
  because `terraform_cli_available` was `false`; this is a real, actionable
  defect now that measurement is trustworthy. Per Decision #9, sub-agent
  *tuning* (the "then" items — per-sub-agent model selection, grounded-brief
  rendering, richer domain corpus) is authorized now that the baseline is
  real, but not yet spec'd into a task file.
- **Phase 7 (Learning & Memory):** enabler complete. `tasks/p79-transcript-memory.md`
  is `done` — per-client transcript ingestion (distill+cite+confirm, reusing
  the existing debrief/confirm_debrief gate) and an isolated per-engagement
  semantic index (`semantic_search`, native-only) are live. Still open, not
  yet spec'd: outcome capture (structured win/loss debrief) and the anonymized
  cross-client knowledge corpus.

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

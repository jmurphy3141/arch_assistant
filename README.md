# OCI Architecture Assistant — Archie

A conversational OCI solutions architect assistant built for SE teams that close deals with technical POCs. Describe a customer workload; Archie plans the right POC, then produces all artifacts simultaneously — architecture diagrams, BOM pricing, POV documents, JEP execution plans, WAF reviews, Terraform, and a client-ready PowerPoint deck.

---

## POC Workflow

**Rough requirements → 3 parallel POC options → pick one → all artifacts in parallel → demo → sale**

```
SE: "Customer is AWS, 200-node K8s, CFO flagged $2M cloud bill, exec review in 3 weeks"

Archie: [explores 3 angles in parallel]
  ├─ Option 1: Oracle DB → ADB migration  (relevance 9/10, 4h build)
  ├─ Option 2: OKE + AI/ML platform       (relevance 7/10, 6h build)
  └─ Option 3: Cost optimization TCO      (relevance 8/10, 3h build)
  → Recommends Option 1: "Customer mentioned cost 3× — DB migration proves it in 4h"

SE: "go with option 1"

Archie: [generates all 5 artifacts in parallel, ~90 seconds]
  ├─ Architecture diagram (.drawio)
  ├─ BOM with OCI pricing (.xlsx)
  ├─ JEP execution plan (markdown)
  ├─ Terraform scripts (.tf)
  └─ Client PowerPoint deck (.pptx)
```

**Background mode:** Kick off POC generation during a meeting. Archie runs in the background and sends a Telegram notification when done.

### Key tools

| Tool | What it does |
|---|---|
| `generate_poc_plan` | Explores 3 POC options in parallel (migration, AI/ML, cost angles), returns ranked options with demo scripts |
| `generate_presentation` | Produces a 7-slide Oracle-standard PowerPoint deck using official OCI icon stencils |
| `generate_diagram` | OCI architecture diagram (.drawio) |
| `generate_bom` | Live OCI pricing BOM (.xlsx) |
| `generate_jep` | JEP execution plan |
| `generate_terraform` | Terraform for the recommended architecture |
| `generate_waf` | WAF security review |
| `generate_pov` | Point-of-Value document |
| `generate_tech_report` | Technical research and service selection |

---

## SkillForge — Reusable Agent Orchestration Framework

**SkillForge** is the lightweight, domain-agnostic framework that powers Archie.
Any team can use it to build multi-agent systems with a consistent chat interface
and an intelligent managing orchestrator — no OCI dependency required.

### Vision

SkillForge enables teams to create powerful agent systems where:
- A central **polymath orchestrator** dynamically wears different expert "hats"
- Behavior is primarily defined through **prompts and skill files** — not Python code
- New domains (AWS, Kubernetes, Sales Ops, etc.) can be onboarded quickly with minimal code

### Core Features

| Feature | What it does |
|---------|-------------|
| **Dynamic Hats** | Orchestrator switches expert roles on demand; each hat injects a full `[ACTIVE EXPERT]` block at the top of the system prompt |
| **Structured Hat Files** | Hats are `.md` files with YAML frontmatter (`hat_rules`, `memory_focus`, `coordination`) and structured sections (Core Principles, Quality Bar, Pre-Action Checklist, Post-Action Review, Output Contract, Critic Evaluation Guidance) |
| **Hat-Specific Memory Views** | Each active hat receives a filtered `[MEMORY VIEW]` built from `memory_focus.priority_fields`; orchestrator retains full canonical memory |
| **Step 3 Planning** | Before the ReAct loop, Forge reasons through goal, memory state, primary architectural risk, and hat selection — producing a structured plan |
| **Expert Pre-Action (Step 4)** | Before calling any hat-gated tool, the orchestrator thinks as the active expert: names the workload pattern, states its recommendation with specifics, identifies gaps and defaults, flags the top risk, and writes a self-contained sub-agent brief |
| **Expert Post-Review (Step 6)** | After a tool returns, the orchestrator reviews the result across four phases: Quality Bar (A), Post-Action Review checklist (B), Memory consistency (C), and architectural soundness (D). Phase D surfaces goal fit, antipatterns, and next-step suggestions |
| **Structured Critic Pass** | After post-review approves, the critic hat applies its Quality Bar per item with PASS/FAIL evidence — no rubber-stamping; one failing check rejects the result |
| **Transition Suggestions** | `hat_rules.when_to_activate` triggers are matched against each turn — Forge emits status events suggesting relevant hats before the ReAct loop starts |
| **Coordination Rules** | `coordination` frontmatter declares `recommended_hats`, `parallel_with`, `handoff_message`, and `synthesis_step` — multi-agent flow lives in skill files, not Python |
| **Skill Files** | Global routing and domain guidance in `skills/*.md` — injected into the system prompt on every turn |
| **Forge Orchestrator** | ReAct loop with memory, delegation, parallel execution, and critique |
| **A2ADelegate** | First-class delegation — wraps any A2A sub-agent endpoint as a callable tool |
| **Parallel Execution** | Native support for running multiple specialists concurrently |
| **Declarative Registration** | Register tools from a YAML config — no boilerplate |
| **Prompt-First Design** | Adding a domain, tuning behavior, or defining coordination patterns requires only editing skill files |

### Architecture

```
Forge (skillforge/forge.py)
  │
  ├─ ToolRegistry          per-tool handler, skill_guidance, critique_enabled
  ├─ Memory (interface)    assemble() → MemorySnapshot, update() → stores artifacts
  ├─ HatEngine             loads agent/hats/*.md, exposes use_hat_* tools
  │
  ├─ run_turn(session_id, user_message, context)
  │     │
  │     ├─ Memory.assemble()              build MemorySnapshot for this turn
  │     ├─ get_transition_suggestions()   emit status events for relevant hats
  │     ├─ _build_active_system_msg()     base prompt + [ACTIVE EXPERT] blocks
  │     ├─ build_memory_view_block()      hat-filtered [MEMORY VIEW] → user prompt
  │     ├─ STEP 3 — Planning             goal, risk, hat selection (before ReAct loop)
  │     ├─ ReAct loop
  │     │     ├─ LLM call (text_runner)
  │     │     ├─ _parse_tool_call()
  │     │     ├─ needs_input / parallel / hat / domain tool dispatch
  │     │     ├─ STEP 4 — Expert Pre-Action   workload pattern, gaps+defaults,
  │     │     │                               recommendation, risk, sub-agent brief
  │     │     ├─ tool handler call
  │     │     ├─ STEP 6 — Expert Post-Review  Phase A Quality Bar · Phase B Post-Action
  │     │     │                               Phase C Memory · Phase D Soundness (advisory)
  │     │     ├─ coordination trigger check → handoff/parallel status events
  │     │     └─ _run_critique_pass()     per-item Quality Bar PASS/FAIL (critique_enabled=True)
  │     └─ Memory.update()               persist artifacts and facts
  │
  └─ invoke_tool(tool_name, args, session_id, context)
        Direct tool call bypassing the LLM loop
```

### Quick Example

```python
from skillforge import Forge, SimpleMemory
import agent.hat_engine as hat_engine

async def my_runner(prompt, system, label=""):
    # your LLM client here
    return await call_my_llm(prompt, system)

forge = Forge(
    base_system_prompt="You are a helpful assistant.",
    hat_engine=hat_engine,
    memory=SimpleMemory(),
    text_runner=my_runner,
)

# Register tools from YAML
forge.register_tools_from_config("forge_tools.yaml")

# Inject global skill guidance
forge.register_skill_file("skills/intent_routing.md")

# Run a turn
result = await forge.run_turn(
    session_id="customer-123",
    user_message="Generate a BOM for my workload",
    context={},
)
print(result.reply)
```

```yaml
# forge_tools.yaml
tools:
  - name: generate_bom
    handler: "agent.tools.bom:BomHandler"
    description: "Generate an OCI BOM"
    skill_guidance: "skills/bom_guidance.md"
    critique_enabled: true

  - name: call_cfn
    handler:
      type: a2a_delegate
      base_url: "http://localhost:9090"
      endpoint: "/a2a"
    description: "Generate CloudFormation templates"
```

### AWS Quickstart

See `examples/aws_quickstart/` for a self-contained example using SkillForge
with zero OCI dependencies.

---

## Archie — OCI Architecture Assistant

Archie is the production implementation of SkillForge for Oracle SA engagements.

### System Architecture

```
User (browser UI)
  │
  ▼
drawing_agent_server.py  ── FastAPI, port 8080
  │  /api/chat + /api/chat/stream   → archie_loop.py via SkillForge Forge
  │  /api/bom/*                     → bom_service.py
  │  /api/upload-bom, /api/clarify  → diagram pipeline
  │  /api/pov, /api/jep, /api/waf   → specialist agents
  │  /api/terraform/*               → terraform sub-agent
  │
  ├─ agent/archie_wiring.py      Wires SkillForge Forge; injects Expert Identity
  │                               (pattern recognition, risk instinct, specificity,
  │                               assumption surfacing, proactive guidance)
  ├─ agent/hat_engine.py         Expert lenses — 8 hats, auto-activates on tool dispatch
  ├─ agent/archie_memory_impl.py Memory adapter — enriches prompts with infrastructure
  │                               profile, constraints, resolved questions
  │
  ├─ Sub-agents (independent A2A services)
  │   ├─ sub_agents/diagram/     port 8082
  │   ├─ sub_agents/bom/         port 8083
  │   ├─ sub_agents/pov/         port 8084
  │   ├─ sub_agents/jep/         port 8085
  │   ├─ sub_agents/waf/         port 8086
  │   └─ sub_agents/terraform/   port 8087
  │
  ├─ Diagram pipeline
  │   ├─ agent/bom_parser.py       BOM → ServiceItem list + LLM prompt
  │   ├─ agent/intent_compiler.py  LayoutIntent → validated spec
  │   ├─ agent/layout_engine.py    Spec → x,y positions
  │   └─ agent/drawio_generator.py Positions → draw.io XML
  │
  └─ Persistence
      ├─ agent/document_store.py            Notes, docs, history, Terraform bundles
      ├─ agent/context_store.py             Per-customer working context
      └─ agent/persistence_objectstore.py   OCI Object Storage adapter
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- OCI Compute instance with Instance Principal auth
- OCI GenAI service endpoint and model OCID

### Install

```bash
git clone https://github.com/jmurphy3141/arch_assistant.git ~/drawing-agent
cd ~/drawing-agent
pip3.11 install -r requirements.txt
```

### Configure

Edit `config.yaml` with your OCI settings. Copy `.env.example` to `.env` and set:

```bash
SESSION_SECRET=$(openssl rand -hex 32)
```

### Start the main server

```bash
cd ~/drawing-agent
SESSION_SECRET=<your-secret> \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &
sleep 3 && curl -s http://localhost:8080/health
```

### Live prompt-to-file validation

Run live prompt-to-file tests against a known local branch server, not an
unknown long-running service. Leave OIDC variables unset so local downloads do
not require a browser session:

```bash
env -u OIDC_CLIENT_ID -u OIDC_CLIENT_SECRET -u OIDC_REDIRECT_URI \
  -u OIDC_ISSUER -u OCI_IDENTITY_DOMAIN_URL \
  python3.11 -m uvicorn drawing_agent_server:app \
    --host 127.0.0.1 --port 18080

RUN_ARCHIE_PROMPT_FILE_LIVE=1 \
AGENT_BASE_URL=http://127.0.0.1:18080 \
pytest tests/test_archie_prompt_to_file_live.py -v -s
```

When validating against an auth-enabled deployed server, provide the browser
session cookie so authenticated artifact download URLs can be fetched:

```bash
RUN_ARCHIE_PROMPT_FILE_LIVE=1 \
AGENT_BASE_URL=https://archie.example.com \
AGENT_SESSION_COOKIE='session=<cookie-value>' \
pytest tests/test_archie_prompt_to_file_live.py -v -s
```

### Start sub-agents

```bash
mkdir -p logs
python3.11 -m uvicorn sub_agents.diagram.server:app   --host 0.0.0.0 --port 8082 > logs/diagram.log 2>&1 &
python3.11 -m uvicorn sub_agents.bom.server:app       --host 0.0.0.0 --port 8083 > logs/bom.log    2>&1 &
python3.11 -m uvicorn sub_agents.pov.server:app       --host 0.0.0.0 --port 8084 > logs/pov.log    2>&1 &
python3.11 -m uvicorn sub_agents.jep.server:app       --host 0.0.0.0 --port 8085 > logs/jep.log    2>&1 &
python3.11 -m uvicorn sub_agents.waf.server:app       --host 0.0.0.0 --port 8086 > logs/waf.log    2>&1 &
python3.11 -m uvicorn sub_agents.terraform.server:app --host 0.0.0.0 --port 8087 > logs/terraform.log 2>&1 &
```

### Build the UI

```bash
cd ui && npm install && npm run build
```

---

## Production Deployment (systemd)

Service files for all processes are in `deploy/`. See `deploy/README.md` for full install instructions.

```bash
sudo cp deploy/oci-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oci-agent oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform
```

### Port Map

| Service | Port |
|---------|------|
| Main server (Archie) | 8080 |
| Diagram sub-agent | 8082 |
| BOM sub-agent | 8083 |
| POV sub-agent | 8084 |
| JEP sub-agent | 8085 |
| WAF sub-agent | 8086 |
| Terraform sub-agent | 8087 |
| Tech Research sub-agent | 8086 |
| POC Strategist sub-agent | 8087 |
| Presentation sub-agent | 8088 |

---

## Configuration

### config.yaml

| Key | Description |
|-----|-------------|
| `region` | OCI region (`us-chicago-1`) |
| `compartment_id` | Compartment OCID for GenAI calls |
| `inference.model_id` | OCI GenAI model OCID |
| `inference.service_endpoint` | OCI GenAI endpoint URL |
| `persistence.bucket_name` | OCI Object Storage bucket (default: `agent_assistante`) |
| `writing.max_tokens` | Token budget for POV/JEP/WAF generation |
| `writing.temperature` | Sampling temperature (default: 0.7) |
| `orchestrator.max_tool_iterations` | Forge ReAct loop max iterations (default: 5) |
| `orchestrator.history_max_turns` | History turns per prompt (default: 30) |

### .env — secrets

| Variable | Required | Description |
|----------|----------|-------------|
| `SESSION_SECRET` | ✅ | Cookie signing key — `openssl rand -hex 32` |
| `OIDC_CLIENT_ID` | for auth | OCI Identity Domain confidential app client ID |
| `OIDC_CLIENT_SECRET` | for auth | Client secret |
| `OIDC_REDIRECT_URI` | for auth | OAuth callback URL |
| `OIDC_ISSUER` | for auth | Identity Domain base URL |
| `OIDC_REQUIRED_GROUP` | optional | Require membership in this group |

---

## API Endpoints

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Single-turn chat |
| `POST` | `/api/chat/stream` | Streaming chat (`?mode=sse` or `?mode=chunked`) |
| `GET` | `/api/chat/{customer_id}/history` | Conversation history |
| `GET` | `/api/chat/history` | Cross-customer history index |
| `DELETE` | `/api/chat/{customer_id}/history` | Clear history |
| `POST` | `/api/chat/{customer_id}/reset-context` | Reset engagement context |

### BOM

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bom/chat` | BOM advisory chat |
| `POST` | `/api/bom/generate-xlsx` | Export BOM as XLSX |
| `GET` | `/api/bom/{customer_id}/download/{filename}` | Download BOM XLSX |

### Diagram

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload-bom` | Upload BOM.xlsx → diagram |
| `POST` | `/api/clarify` | Submit clarification answers |
| `POST` | `/api/generate` | Generate from JSON resource list |
| `POST` | `/api/refine` | Refine existing diagram |
| `GET` | `/api/download/{filename}` | Download `.drawio` file |

### Specialists

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/pov/generate` | Generate POV document |
| `POST` | `/api/jep/generate` | Generate JEP |
| `POST` | `/api/jep/approve` | Approve JEP |
| `POST` | `/api/jep/kickoff` | Generate kickoff questions |
| `POST` | `/api/jep/revision-request` | Request revision |
| `POST` | `/api/waf/generate` | Generate WAF review |
| `POST` | `/api/terraform/generate` | Generate Terraform bundle |
| `GET` | `/api/terraform/{customer_id}/download/{filename}` | Download Terraform file |

### Notes, Context & System

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/notes/upload` | Upload meeting notes |
| `GET` | `/api/notes/{customer_id}` | List notes |
| `GET` | `/api/context/{customer_id}` | Read engagement context |
| `GET` | `/health` | Health check |
| `GET` | `/.well-known/agent.json` | A2A agent card |

---

## Hat System

Expert lenses in `agent/hats/` are structured `.md` files with YAML frontmatter
and named sections. When a hat activates, its full expert context is prepended to
the system prompt as an `[ACTIVE EXPERT]` block. Each hat also receives a
filtered `[MEMORY VIEW]` containing only the facts most relevant to its role.

### Active Hats

| Hat | Role | Auto-activated by |
|-----|------|:-----------------:|
| `critic` | Per-item Quality Bar review; approves or injects revision prompt | any `critique_enabled` tool |
| `governor` | Deterministic guardrails for cost, security, compliance | manual |
| `oci_bom_expert` | BOM sizing, SKU selection, pricing validation | `generate_bom` |
| `diagram_for_oci` | OCI topology, traffic path, icon standards | `generate_diagram` |
| `oci_waf_reviewer` | WAF pillar coverage, P1 severity checks | `generate_waf` |
| `terraform_for_oci` | HCL validation, provider version, compartment strategy | `generate_terraform` |
| `oci_customer_pov_writer` | POV document quality, press release + FAQ sections | `generate_pov` |
| `jep_writer` | JEP document quality, success criteria, scope | `generate_jep` |

### Hat File Format

Each hat file uses YAML frontmatter (machine-readable) followed by structured
markdown sections injected into the LLM system prompt at expert-block build time:

```markdown
---
version: "1.0"
display_name: "OCI BOM Expert"
hat_rules:
  when_to_activate: ["user asks about cost, pricing, BOM, or budget"]
  can_hand_off_to: ["diagram_for_oci", "terraform_for_oci"]
  suggested_next_hat: "diagram_for_oci"
memory_focus:
  priority_fields: ["sizing", "cost_assumptions", "budget", "region"]
  include_full_memory: false
  emphasis: "Focus on quantities, pricing, and sizing gaps."
coordination:
  triggers: ["BOM generation is complete"]
  recommended_hats: ["diagram_for_oci"]
  parallel_with: []
  handoff_message: "BOM review complete. Suggesting diagram generation next."
---

# OCI BOM Expert Hat

## Core Principles        ← expert mindset and invariants
## Quality Bar            ← per-item checklist applied in critic pass and post-review Phase A
## Pre-Action Checklist   ← what the expert confirms before calling the sub-agent (Step 4)
## Post-Action Review     ← what the expert checks after the sub-agent returns (Step 6 Phase B)
## Output Contract        ← required fields in a valid result
## Critic Evaluation Guidance  ← specific guidance for the critic hat's review
## Failure Questions      ← questions to surface if the result is rejected
## Activation & Drop      ← when this hat activates and when it drops
```

All sections are optional but **Pre-Action Checklist** and **Post-Action Review** are what
drive the expert reasoning loop (Steps 4 and 6). A hat without them will still work but
the expert pre-action and post-review will operate without domain-specific checklists.

See `skills/SKILL_TEMPLATE.md` for the full format reference.

### Adding a New Hat

Drop a `.md` file into `agent/hats/` — no Python changes required. SkillForge
auto-discovers it and exposes `use_hat_{name}` / `drop_hat_{name}` tool calls.

---

## Expert Reasoning Loop

When the orchestrator wears a hat, Forge runs a structured reasoning sequence around
every tool call. This is what makes the orchestrator feel like a senior expert, not a
structured router.

### Step 3 — Planning (before the ReAct loop)

Before entering the ReAct loop, Forge reasons through:

- **STEP 1 — UNDERSTAND:** Identifies the deliverable, whether it's new or a revision,
  what's ambiguous, and **the primary architectural risk** (HA exposure, budget ceiling,
  public ingress without filtering, compliance scope, etc.)
- **STEP 2 — MEMORY ASSESSMENT:** What facts are confirmed vs. missing; whether there's
  enough to produce a complete deliverable or questions must be asked first
- **STEP 3 — PLAN + HAT SELECTION:** Which hat to activate and why; execution plan

### Step 4 — Expert Pre-Action

Before calling any hat-gated tool, the orchestrator thinks as the expert the hat defines:

| Section | Content |
|---------|---------|
| **KNOWN FACTS** | Every confirmed value from memory and conversation — shapes, region, sizing, HA mode, budget, compliance scope. Specific values only. |
| **GAPS** | Every unconfirmed item from the hat's `## Pre-Action Checklist`. For each: state the default and why it's safe. Unsafe-to-default items become `NEEDS_CLARIFICATION`. |
| **EXPERT ASSESSMENT** | Workload pattern name → exact recommendation (specific services, shapes, SKUs) → why this over the main alternative → top risk and mitigation → proactive flag for the customer |
| **SUB-AGENT TASK** | Complete, self-contained task brief for the sub-agent. Includes all confirmed values and defaults. No context references ("as discussed") — fully specified. |

### Step 6 — Expert Post-Review

After the tool returns, the orchestrator reviews the result across four phases:

| Phase | What it checks | Output |
|-------|---------------|--------|
| **A — Quality Bar** | Each item in the hat's `## Quality Bar` section | `PASS` or `FAIL: <specific value>` per item |
| **B — Post-Action Review** | Each item in the hat's `## Post-Action Review` section | `PASS` or `FAIL: <field and expected value>` per item |
| **C — Memory consistency** | Result values against confirmed memory snapshot | `CONSISTENT` or `CONFLICT: <field> expected=X got=Y` |
| **D — Architectural soundness** | Is this the right output for this customer? | GOAL FIT · ANTIPATTERNS · NEXT STEP FLAG (advisory — does not change routing) |

Final decision: `EXPERT_APPROVED` / `EXPERT_ITERATE: <issue>` / `EXPERT_SURFACE: <issue>`

### Critic Pass (after post-review approves)

The critic hat applies its per-tool validation schema item by item. One failing check
rejects the result with the specific field name and what was wrong. The critic can only
call `critic_approve` — no other tool. This is the final quality gate before the result
reaches the user.

---

## Skills

Global skill files in `skills/` are injected into the system prompt on every turn.

| File | Purpose |
|------|---------|
| `skills/intent_routing.md` | When to respond conversationally vs call a tool |
| `skills/SKILL_TEMPLATE.md` | Full format reference for hat and global skill files |

---

## OCI Environment

| Setting | Value |
|---------|-------|
| Host | `opc@10.0.3.47` |
| Port | 8080 (internal), 443 (nginx) |
| Python | `python3.11` |
| Auth | Instance Principal |
| Region | `us-chicago-1` |
| Bucket | `agent_assistante` (namespace: `oraclejamescalise`) |

---

## Run Tests

```bash
pytest tests/ -v -m "not live"
```

---

## Open Issues

See [GitHub Issues](https://github.com/jmurphy3141/arch_assistant/issues) for
current bugs and planned improvements.

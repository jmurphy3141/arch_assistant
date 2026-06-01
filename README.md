# SkillForge + Archie

**SkillForge** is a lightweight, domain-agnostic agent orchestration framework.
**Archie** is the OCI Architecture Manager — Oracle's production implementation of SkillForge for SE teams.

---

## What Is SkillForge?

SkillForge gives you a managing orchestrator that:

- Wears **expert hats** — markdown files that inject deep domain knowledge into the reasoning loop
- Runs a structured **expert reasoning loop** — plans before acting, thinks deeply before calling tools, reviews critically after
- Delegates to **specialist sub-agents** via A2A over HTTP — each sub-agent runs independently
- Dispatches **parallel artifact generation** natively — fan out to five specialists at once
- Keeps **all business logic in prompts and skill files** — adding a new domain means writing markdown, not Python

SkillForge has zero domain knowledge. `import oci`, `import boto3`, or any domain SDK in `skillforge/` is a boundary violation. Domain knowledge lives in registered tool handlers, hat files, and a `Memory` implementation.

**Archie is one example.** The same framework can power an AWS infrastructure manager, a Kubernetes operations center, a sales operations assistant, or any other domain that benefits from a knowledgeable conversational orchestrator.

---

## Archie — OCI Architecture Manager

Archie is the production SkillForge implementation for Oracle SA teams. Describe a customer workload; Archie plans the right POC, then produces all artifacts simultaneously.

### POC Workflow

```
SE: "Customer is AWS, 200-node K8s, CFO flagged $2M cloud bill, exec review in 3 weeks"

Archie: [wears POC Strategist hat — runs 3 parallel explorations]
  Option 1: Oracle DB → ADB migration    9/10 relevance  4h build
  Option 2: OKE + AI/ML platform         7/10 relevance  6h build
  Option 3: Cost optimization TCO        8/10 relevance  3h build
  → Recommended: Option 1 — "CFO mentioned cost 3× and ADB migration proves it in 4h"

SE: "go with option 1"

Archie: [fans out all 5 artifacts simultaneously, ~90 seconds]
  ├─ Architecture diagram  (.drawio)
  ├─ BOM with OCI pricing  (.xlsx)
  ├─ JEP execution plan    (markdown)
  ├─ Terraform scripts     (.tf)
  └─ Client PowerPoint     (.pptx)
```

**Background mode:** Kick off generation during a meeting. Archie runs in the background and sends a Telegram notification with the top recommendation when done.

### Archie's Tools

| Tool | What it does |
|---|---|
| `generate_poc_plan` | Explores 3 POC angles in parallel, returns ranked options with demo scripts and risk assessments |
| `generate_diagram` | OCI architecture diagram (.drawio) with official icon stencils |
| `generate_bom` | Live OCI pricing BOM (.xlsx) |
| `generate_jep` | JEP execution plan (markdown) |
| `generate_terraform` | Terraform bundle for the recommended architecture |
| `generate_waf` | WAF security review |
| `generate_pov` | Point-of-Value document |
| `generate_tech_report` | Technical research and service selection |
| `generate_sales_deck` | Sales enablement deck |
| `generate_presentation` | 7-slide client-ready PowerPoint using Oracle OCI icon stencils |

---

## How to Build Your Own Manager

A SkillForge manager has four parts: a `Forge` instance, tool handlers, hats, and a `Memory` implementation.

### 1. Wire up Forge

```python
from skillforge import Forge, SimpleMemory
import agent.hat_engine as hat_engine

forge = Forge(
    base_system_prompt="You are an AWS solutions architect...",
    hat_engine=hat_engine,
    memory=SimpleMemory(),
    text_runner=my_llm_call,          # async (prompt, system, label) -> str
)
```

### 2. Register tools

```python
from skillforge import ArgSchema

forge.register_tool(
    "size_ec2",
    ec2_sizing_handler,               # async (args, *, memory, context, trace_id) -> ToolResult
    description="Size EC2 instances for a workload.",
    memory_contract=True,             # handler receives MemorySnapshot
    critique_enabled=True,            # critic hat reviews the result
    requires_hat="aws_sizing_expert", # expert hat auto-activates before this tool
    args={
        "workload": ArgSchema(description="Workload description", type="string"),
    },
)
```

Or declaratively from YAML:

```yaml
# forge_tools.yaml
tools:
  - name: size_ec2
    handler: "myapp.tools.ec2:EC2SizingHandler"
    description: "Size EC2 instances for a workload."
    critique_enabled: true

  - name: generate_cfn
    handler:
      type: a2a_delegate
      base_url: "http://localhost:9090"
    description: "Generate CloudFormation templates."
```

### 3. Write a hat

Drop a `.md` file in `agent/hats/`. No Python changes needed — SkillForge auto-discovers it.

```markdown
---
version: "1.0"
display_name: "AWS Sizing Expert"
hat_rules:
  when_to_activate: ["user asks about EC2 sizing or instance types"]
memory_focus:
  priority_fields: ["workload_type", "region", "concurrency", "budget"]
  include_full_memory: false
---

# AWS Sizing Expert

## Core Principles
You recognize compute patterns immediately: "web tier with 10k concurrent" → c6g.xlarge
candidates with ALB. "batch processing" → spot fleet with checkpointing.

## Pre-Action Checklist
Before calling the sizing tool, confirm:
- workload_type: required. If absent → NEEDS_CLARIFICATION: "What workload type?"
- region: default us-east-1 if absent — document assumption.

## Post-Action Review
Verify: instance type is available in region, cost is within budget signal if stated.
```

### 4. Run a turn

```python
result = await forge.run_turn(
    session_id="customer-abc",
    user_message="Size a 3-tier web app for 10k concurrent users",
    context={},
)
print(result.reply)
```

### Full working example

See `examples/aws_quickstart/` — a self-contained manager with zero OCI dependencies.

---

## SkillForge Architecture

```
Forge (skillforge/forge.py)
  │
  ├─ ToolRegistry         tool handlers, hat requirements, critique flags
  ├─ Memory (interface)   assemble() → MemorySnapshot per turn
  ├─ HatEngine            loads agent/hats/*.md; exposes use_hat_* tools
  │
  └─ run_turn(session_id, user_message, context)
        │
        ├─ Memory.assemble()           build MemorySnapshot
        ├─ STEP 3 — Planning           goal · risk · hat selection
        ├─ ReAct loop
        │     ├─ LLM call
        │     ├─ STEP 4 — Pre-Action   KNOWN FACTS → GAPS → EXPERT ASSESSMENT → SUB-AGENT TASK
        │     ├─ tool handler call
        │     ├─ STEP 6 — Post-Review  Quality Bar · Checklist · Memory · Soundness
        │     └─ Critic pass           per-item PASS/FAIL; rejects on any failure
        └─ Memory.update()
```

### Expert Reasoning Loop

Every tool call goes through four steps:

**Step 3 — Planning** (before the loop): Forge reasons through the goal, memory state, primary architectural risk, and which hat to activate.

**Step 4 — Expert Pre-Action**: Wearing the hat, the orchestrator produces:
- `KNOWN FACTS` — every confirmed value from memory
- `GAPS` — every unconfirmed field; state the default and whether it's safe to assume
- `EXPERT ASSESSMENT` — workload pattern → specific recommendation → top risk → proactive flag
- `SUB-AGENT TASK` — a complete, self-contained brief for the sub-agent (no context references)

**Step 6 — Expert Post-Review**: Four phases after the tool returns:
- **Phase A** — Quality Bar: per-item PASS/FAIL against the hat's checklist
- **Phase B** — Post-Action Review: domain-specific correctness checks
- **Phase C** — Memory consistency: result values vs. confirmed memory
- **Phase D** — Architectural soundness (advisory): goal fit, antipatterns, next step

**Critic Pass**: After post-review approves, the critic hat applies per-tool validation schemas. One failing check rejects the result. This is the final quality gate.

---

## Hat System

Expert hats live in `agent/hats/` as structured markdown files. When activated, the hat's content is prepended to the system prompt as an `[ACTIVE EXPERT]` block, and the hat's `memory_focus.priority_fields` filter the `[MEMORY VIEW]` the expert receives.

### Archie's Hats

| Hat | Auto-activated by | Role |
|-----|:-----------------:|------|
| `oci_poc_strategist` | `generate_poc_plan` | Pattern recognition, deal-stage awareness, risk anticipation, POC success criteria |
| `oci_bom_expert` | `generate_bom` | BOM sizing, SKU selection, pricing validation |
| `diagram_for_oci` | `generate_diagram` | OCI topology, traffic paths, icon standards |
| `oci_waf_reviewer` | `generate_waf` | WAF pillar coverage, P1 severity checks |
| `terraform_for_oci` | `generate_terraform` | HCL correctness, provider versions, compartment strategy |
| `oci_customer_pov_writer` | `generate_pov` | POV document quality, executive narrative |
| `jep_writer` | `generate_jep` | JEP scope, success criteria, phased execution |
| `oci_presentation_writer` | `generate_presentation` | Synthesis of BOM + research + diagrams into a client deck |
| `infra_tech_research` | `generate_tech_report` | Service selection, technology research |
| `critic` | any `critique_enabled` tool | Per-item Quality Bar review |
| `governor` | manual | Cost, security, and compliance guardrails |

### Hat File Format

```markdown
---
version: "1.0"
display_name: "Expert Name"
hat_rules:
  when_to_activate: ["trigger phrases or tool names"]
  can_hand_off_to: ["other_hat_name"]
memory_focus:
  priority_fields: ["field1", "field2"]
  include_full_memory: false
coordination:
  parallel_with: ["other_tool"]
  suggested_next_hat: "next_hat"
---

# Expert Name Hat

## Core Principles        ← expert mindset and decision heuristics
## Quality Bar            ← per-item checklist for Phase A post-review
## Pre-Action Checklist   ← what the expert confirms before calling the sub-agent (Step 4)
## Post-Action Review     ← what the expert verifies after the result returns (Step 6)
## Output Contract        ← required fields in a valid result
## Critic Evaluation Guidance
## Failure Questions
## Activation & Drop
```

Adding a hat requires only creating the `.md` file — no Python changes.

---

## Archie System Architecture

```
Browser (React/Vite UI)
  │
  ▼
drawing_agent_server.py      FastAPI, port 8080
  │  /api/chat/stream         streaming chat
  │  /api/chat/background     background job + Telegram notification
  │  /api/job/{id}            job status polling
  │  /download                artifact download (diagram, BOM, PPTX, Terraform)
  │  /health
  │  (route inventory: docs/backend-api-surface.md)
  │
  ├─ archie_session.py        thin session wrapper: load context → forge.run_turn() → save
  ├─ agent/archie_wiring.py   build_forge(): Archie system prompt + 10 tools + hat engine
  │
  ├─ SkillForge (skillforge/)
  │   └─ forge.py             ReAct loop, expert reasoning, parallel dispatch, critique
  │
  ├─ Sub-agents (independent A2A HTTP services)
  │   ├─ sub_agents/diagram/       port 8082
  │   ├─ sub_agents/bom/           port 8083
  │   ├─ sub_agents/pov/           port 8084
  │   ├─ sub_agents/jep/           port 8085
  │   ├─ sub_agents/waf/           port 8086
  │   ├─ sub_agents/terraform/     port 8087
  │   ├─ sub_agents/tech_research/ port 8088
  │   └─ sub_agents/sales_deck/    port 8089
  │
  ├─ Diagram pipeline
  │   ├─ agent/bom_parser.py        BOM → ServiceItem list + LLM prompt
  │   ├─ agent/intent_compiler.py   LayoutIntent → validated spec
  │   ├─ agent/layout_engine.py     Spec → x,y positions
  │   └─ agent/drawio_generator.py  Positions → draw.io XML
  │
  └─ Persistence
      ├─ agent/document_store.py           Notes, docs, history, artifacts
      ├─ agent/context_store.py            Per-customer engagement context
      └─ agent/persistence_objectstore.py  OCI Object Storage adapter
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

Edit `config.yaml` with your OCI resource OCIDs and endpoints. Copy `.env.example` to `.env`:

```bash
SESSION_SECRET=$(openssl rand -hex 32)
```

### Start services

```bash
# Main server
SESSION_SECRET=<your-secret> \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &

# Sub-agents
mkdir -p logs
python3.11 -m uvicorn sub_agents.diagram.server:app       --host 0.0.0.0 --port 8082 > logs/diagram.log 2>&1 &
python3.11 -m uvicorn sub_agents.bom.server:app           --host 0.0.0.0 --port 8083 > logs/bom.log    2>&1 &
python3.11 -m uvicorn sub_agents.pov.server:app           --host 0.0.0.0 --port 8084 > logs/pov.log    2>&1 &
python3.11 -m uvicorn sub_agents.jep.server:app           --host 0.0.0.0 --port 8085 > logs/jep.log    2>&1 &
python3.11 -m uvicorn sub_agents.waf.server:app           --host 0.0.0.0 --port 8086 > logs/waf.log    2>&1 &
python3.11 -m uvicorn sub_agents.terraform.server:app     --host 0.0.0.0 --port 8087 > logs/terraform.log 2>&1 &
python3.11 -m uvicorn sub_agents.tech_research.server:app --host 0.0.0.0 --port 8088 > logs/research.log 2>&1 &
python3.11 -m uvicorn sub_agents.sales_deck.server:app    --host 0.0.0.0 --port 8089 > logs/sales.log   2>&1 &

# Check health
curl -s http://localhost:8080/health
```

### Build the UI

```bash
cd ui && npm install && npm run build
```

### Production deployment (systemd)

```bash
sudo cp deploy/oci-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oci-agent oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform
```

See `deploy/README.md` for full instructions.

---

## Port Map

| Service | Port |
|---------|------|
| Main server (Archie) | 8080 |
| Diagram sub-agent | 8082 |
| BOM sub-agent | 8083 |
| POV sub-agent | 8084 |
| JEP sub-agent | 8085 |
| WAF sub-agent | 8086 |
| Terraform sub-agent | 8087 |
| Tech Research sub-agent | 8088 |
| Sales Deck sub-agent | 8089 |

---

## Configuration Reference

### config.yaml

| Key | Description |
|-----|-------------|
| `region` | OCI region (`us-chicago-1`) |
| `compartment_id` | Compartment OCID for GenAI calls |
| `inference.model_id` | OCI GenAI model OCID |
| `inference.service_endpoint` | OCI GenAI endpoint URL |
| `persistence.bucket_name` | OCI Object Storage bucket |
| `orchestrator.max_tool_iterations` | Forge ReAct loop max iterations (default: 5) |
| `orchestrator.history_max_turns` | History turns per prompt (default: 30) |
| `telegram.enabled` | Enable Telegram notifications (default: false) |

### .env — secrets

| Variable | Required | Description |
|----------|----------|-------------|
| `SESSION_SECRET` | ✅ | Cookie signing key — `openssl rand -hex 32` |
| `OIDC_CLIENT_ID` | for auth | OCI Identity Domain client ID |
| `OIDC_CLIENT_SECRET` | for auth | Client secret |
| `OIDC_REDIRECT_URI` | for auth | OAuth callback URL |
| `TELEGRAM_BOT_TOKEN` | optional | Telegram bot token for background notifications |
| `TELEGRAM_CHAT_ID` | optional | Telegram chat/group ID |

---

## API Reference

### Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Single-turn chat |
| `POST` | `/api/chat/stream` | Streaming chat (SSE) |
| `POST` | `/api/chat/background` | Background job — returns 202 + job_id |
| `GET` | `/api/job/{job_id}` | Background job status polling |
| `GET` | `/api/chat/{customer_id}/history` | Conversation history |
| `DELETE` | `/api/chat/{customer_id}/history` | Clear history |

### Artifacts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/download` | Download any artifact by key (`.drawio`, `.xlsx`, `.tf`, `.pptx`) |
| `GET` | `/api/terraform/{customer_id}/download/{filename}` | Download Terraform file |

### BOM

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bom/chat` | BOM advisory chat |
| `POST` | `/api/bom/generate-xlsx` | Export BOM as XLSX |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/.well-known/agent.json` | A2A agent card |

---

## Run Tests

```bash
pytest tests/ -v -m "not live"
```

Live prompt-to-file tests (requires running server):

```bash
RUN_ARCHIE_PROMPT_FILE_LIVE=1 \
AGENT_BASE_URL=http://127.0.0.1:18080 \
pytest tests/test_archie_prompt_to_file_live.py -v -s
```

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

## Repository Structure

```
arch_assistant/
├── drawing_agent_server.py     FastAPI server — main entry point
├── config.yaml                 All non-secret server config
├── requirements.txt
│
├── skillforge/                 Domain-agnostic orchestration framework
│   ├── forge.py                Forge class — run_turn(), reasoning loop
│   ├── registry.py             Tool registration
│   ├── types.py                TurnResult, ToolResult, MemorySnapshot, ...
│   └── protocols.py            ToolHandler, Memory, HatEngine interfaces
│
├── agent/
│   ├── archie_session.py       Thin session wrapper: load → forge.run_turn() → save
│   ├── archie_wiring.py        build_forge(): Archie identity + tool registration
│   ├── hat_engine.py           Loads hats, exposes use_hat_* tools
│   ├── hats/                   Expert lenses (.md files)
│   ├── tools/                  Forge tool handlers (one file per tool)
│   └── ...                     BOM pipeline, diagram pipeline, persistence
│
├── sub_agents/                 Independent A2A specialist services
│   ├── {name}/server.py        FastAPI A2A handler
│   ├── {name}/system_prompt.md Sub-agent's own instructions
│   └── {name}/config.yaml      Port, LLM config
│
├── skills/                     Global skill files (injected every turn)
├── examples/aws_quickstart/    Minimal non-OCI example
├── ui/                         React + Vite frontend
├── tests/                      pytest test suite
├── tasks/                      Codex implementation task files (p1–p55)
└── docs/                       Architecture specs and requirements
```

---

## Contributing / Codex

Implementation work is tracked in `tasks/` as `p{N}-{name}.md` files.
Each task file contains exact file paths, code changes, and runnable acceptance criteria.
See `AGENTS.md` for the current architecture reference and development workflow.

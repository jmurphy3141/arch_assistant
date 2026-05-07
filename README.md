# OCI Architecture Assistant — Archie

A conversational OCI solutions architect assistant. An SA describes a customer
workload; Archie produces architecture diagrams, BOM pricing, POV documents,
JEP documents, WAF reviews, and Terraform — in one chat session.

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
| **Dynamic Hats** | Orchestrator switches expert roles on demand with proper lifecycle management |
| **Skill Files** | Domain expertise defined in clean `.md` files, injected into the system prompt |
| **Forge Orchestrator** | ReAct loop with memory, delegation, parallel execution, and critique |
| **A2ADelegate** | First-class delegation — wraps any A2A sub-agent endpoint as a callable tool |
| **Parallel Execution** | Native support for running multiple specialists concurrently |
| **Critique Pass** | Auto-runs a reviewer hat after any `critique_enabled=True` tool call |
| **Declarative Registration** | Register tools from a YAML config — no boilerplate |
| **Prompt-First Design** | Most new features and behavior changes happen in skill files, not code |

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
  │     ├─ Memory.assemble()        build MemorySnapshot for this turn
  │     ├─ _get_system_msg()        base prompt + global skills + tool guidance
  │     ├─ ReAct loop
  │     │     ├─ LLM call (text_runner)
  │     │     ├─ _parse_tool_call()
  │     │     ├─ needs_input / parallel / hat / domain tool dispatch
  │     │     └─ _run_critique_pass()   if critique_enabled=True
  │     └─ Memory.update()          persist artifacts and facts
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
  ├─ agent/archie_loop.py        ReAct loop — all tool dispatch via Forge
  ├─ agent/archie_wiring.py      Wires SkillForge Forge for Archie sessions
  ├─ agent/hat_engine.py         Expert lenses (critic, governor, bom_reviewer…)
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

Expert lenses in `agent/hats/` activate automatically when `critique_enabled=True`
is set on a tool, or can be invoked manually via `use_hat_*` tools.

| Hat | Purpose |
|-----|---------|
| `critic` | Reviews output and approves or injects critique |
| `governor` | Guardrail lens for cost/security/quality |
| `diagram_builder` | Diagram construction guidance |
| `bom_reviewer` | BOM accuracy and completeness review |
| `waf_reviewer` | WAF pillar coverage review |
| `terraform_reviewer` | Terraform structure and security review |

---

## Skills

Routing and domain guidance in `skills/`:

| File | Purpose |
|------|---------|
| `skills/intent_routing.md` | When to respond conversationally vs call a tool |
| `skills/README.md` | Skills authoring guide |

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

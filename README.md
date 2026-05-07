# OCI Architecture Assistant — Archie

A conversational OCI solutions architect assistant. An SA describes a customer
workload; Archie produces architecture diagrams, BOM pricing, POV documents,
JEP documents, WAF reviews, and Terraform — in one chat session.

---

## Architecture

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
  ├─ skillforge/                 Domain-agnostic ReAct orchestrator framework
  │   ├─ forge.py                Forge class — run_turn, invoke_tool, register_tool
  │   ├─ registry.py             ToolRegistry with skill_guidance and critique_enabled
  │   ├─ memory.py               SimpleMemory — zero-config in-memory Memory
  │   ├─ delegate.py             A2ADelegate — wraps A2A sub-agent endpoints as tools
  │   └─ types.py                ToolResult, TurnResult, MemorySnapshot, ParallelToolCall
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
  │   ├─ agent/bom_parser.py     BOM → ServiceItem list + LLM prompt
  │   ├─ agent/intent_compiler.py  LayoutIntent → validated spec
  │   ├─ agent/layout_engine.py  Spec → x,y positions
  │   └─ agent/drawio_generator.py  Positions → draw.io XML
  │
  └─ Persistence
      ├─ agent/document_store.py   Notes, docs, history, Terraform bundles
      ├─ agent/context_store.py    Per-customer working context
      └─ agent/persistence_objectstore.py  OCI Object Storage adapter
```

---

## Quick Start

### Prerequisites

- Python 3.11+ (OCI ADK requires 3.11)
- OCI Compute instance with Instance Principal auth configured
- OCI GenAI service endpoint and model OCID

### Install

```bash
git clone https://github.com/jmurphy3141/arch_assistant.git ~/drawing-agent
cd ~/drawing-agent
pip3.11 install -r requirements.txt
```

### Configure

Edit `config.yaml` with your OCI settings (region, model OCID, compartment, bucket).
Copy `.env.example` to `.env` and set `SESSION_SECRET`:

```bash
openssl rand -hex 32   # use this value for SESSION_SECRET
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

Service files for all processes are in `deploy/`. See `deploy/README.md` for
full install instructions.

```bash
sudo cp deploy/oci-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oci-agent oci-bom oci-diagram oci-pov oci-jep oci-waf oci-terraform
```

### Port map

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
| `writing.temperature` | Sampling temperature for document writing (default: 0.7) |
| `orchestrator.max_tool_iterations` | Forge ReAct loop max iterations (default: 5) |
| `orchestrator.history_max_turns` | History turns per prompt (default: 30) |
| `sub_agents.diagram` | Diagram sub-agent URL (default: `http://localhost:8082`) |
| `sub_agents.bom` | BOM sub-agent URL |
| `sub_agents.terraform` | Terraform sub-agent URL |

### .env — secrets

| Variable | Required | Description |
|----------|----------|-------------|
| `SESSION_SECRET` | ✅ | Cookie signing key — `openssl rand -hex 32` |
| `OIDC_CLIENT_ID` | for auth | OCI Identity Domain confidential app client ID |
| `OIDC_CLIENT_SECRET` | for auth | Client secret |
| `OIDC_REDIRECT_URI` | for auth | OAuth callback URL |
| `OIDC_ISSUER` | for auth | Identity Domain base URL |
| `OIDC_REQUIRED_GROUP` | optional | Require membership in this group |

Auth is enabled automatically when OIDC variables are set. Leave unset to run without auth.

---

## API Endpoints

### Chat (primary path)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Single-turn chat: `{customer_id, customer_name, message}` |
| `POST` | `/api/chat/stream` | Streaming chat (`?mode=sse` or `?mode=chunked`) |
| `GET` | `/api/chat/{customer_id}/history` | Conversation history |
| `GET` | `/api/chat/history` | Cross-customer history index |
| `GET` | `/api/chat/projects` | Project index |
| `DELETE` | `/api/chat/{customer_id}/history` | Clear history |
| `POST` | `/api/chat/{customer_id}/reset-context` | Reset engagement context |

### BOM

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/bom/health` | BOM service health |
| `POST` | `/api/bom/chat` | BOM advisory chat |
| `POST` | `/api/bom/generate-xlsx` | Export BOM as XLSX |
| `GET` | `/api/bom/{customer_id}/download/{filename}` | Download BOM XLSX |
| `POST` | `/api/bom/refresh-data` | Refresh BOM pricing cache |

### Diagram

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/upload-bom` | Upload BOM.xlsx → diagram |
| `POST` | `/api/clarify` | Submit clarification answers |
| `POST` | `/api/generate` | Generate from JSON resource list |
| `POST` | `/api/refine` | Refine existing diagram |
| `GET` | `/api/download/{filename}` | Download `.drawio` file |
| `GET` | `/api/job/{job_id}` | Poll async job status |

### Specialist Agents

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/pov/generate` | Generate POV document |
| `GET` | `/api/pov/{customer_id}/latest` | Latest POV |
| `GET` | `/api/pov/{customer_id}/versions` | All POV versions |
| `POST` | `/api/pov/approve` | Approve POV |
| `POST` | `/api/jep/generate` | Generate JEP |
| `GET` | `/api/jep/{customer_id}/latest` | Latest JEP |
| `POST` | `/api/jep/approve` | Approve JEP |
| `POST` | `/api/jep/kickoff` | Generate kickoff questions |
| `POST` | `/api/jep/answers` | Save kickoff answers |
| `POST` | `/api/jep/revision-request` | Request revision |
| `POST` | `/api/waf/generate` | Generate WAF review |
| `GET` | `/api/waf/{customer_id}/latest` | Latest WAF review |
| `POST` | `/api/terraform/generate` | Generate Terraform bundle |
| `GET` | `/api/terraform/{customer_id}/latest` | Latest Terraform bundle |
| `GET` | `/api/terraform/{customer_id}/download/{filename}` | Download Terraform file |

### Notes & Context

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/notes/upload` | Upload meeting notes |
| `GET` | `/api/notes/{customer_id}` | List notes |
| `GET` | `/api/context/{customer_id}` | Read engagement context |

### Discovery & System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/config` | UI configuration |
| `GET` | `/.well-known/agent.json` | A2A agent card |
| `POST` | `/api/a2a/task` | A2A skill dispatch |

---

## SkillForge Framework

`skillforge/` is a domain-agnostic ReAct orchestrator that Archie is built on.
It can be used independently by any agent team.

### Key concepts

- **Forge** — orchestrates LLM + tool calls in a ReAct loop
- **ToolRegistry** — stores tool handlers with `skill_guidance` and `critique_enabled`
- **A2ADelegate** — wraps any A2A sub-agent endpoint as a tool callable
- **SimpleMemory** — zero-config in-memory Memory for new agent teams
- **Parallel tools** — `ToolResult(status="parallel", parallel_tools=[...])` triggers concurrent dispatch
- **Skill files** — `.md` files injected into system prompt via `register_skill_file()`
- **Critique pass** — `critique_enabled=True` tools auto-trigger a critic hat review after `status="ok"`

### Declarative registration

```python
forge.register_tools_from_config("forge_tools.yaml")
```

```yaml
# forge_tools.yaml
tools:
  - name: generate_bom
    handler: "agent.tools.bom:BomHandler"
    description: "Generate an OCI BOM"
    skill_guidance: "skills/bom_guidance.md"
    critique_enabled: true
```

### AWS quickstart example

See `examples/aws_quickstart/` for a self-contained example using SkillForge
with zero OCI dependencies.

---

## Hat System

Expert lenses in `agent/hats/` are loaded by `hat_engine.py` and exposed as
`use_hat_*` tools. When `critique_enabled=True` is set on a tool, Forge
automatically activates the `critic` hat after the tool returns `status="ok"`.

Available hats:

| Hat | Purpose |
|-----|---------|
| `critic` | Reviews specialist output and approves or injects critique |
| `governor` | Guardrail lens for cost/security/quality |
| `diagram_builder` | Diagram construction guidance |
| `bom_reviewer` | BOM accuracy and completeness review |
| `waf_reviewer` | WAF pillar coverage review |
| `terraform_reviewer` | Terraform structure and security review |

---

## Skills

Routing and domain guidance lives in `skills/`:

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
| App dir | `~/drawing-agent/` |
| Python | `python3.11` |
| Auth | Instance Principal |
| Region | `us-chicago-1` |
| Object Storage bucket | `agent_assistante` (namespace: `oraclejamescalise`) |

---

## Run Tests

```bash
pytest tests/ -v -m "not live"

# Include live OCI tests
RUN_LIVE_TESTS=1 pytest tests/ -v
```

---

## Open Issues

See [GitHub Issues](https://github.com/jmurphy3141/arch_assistant/issues) for
current bugs and planned improvements.

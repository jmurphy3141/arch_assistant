# OCI Drawing Agent (Agent 3 — v1.3.2)

Converts an Excel Bill of Materials (BOM) into a fully-editable draw.io OCI
architecture diagram. Correct OCI icon stencils, VCN topology, subnets, and
gateways — all in one Python process.

```
BOM.xlsx + optional context file
  ↓
drawing_agent_server.py  (FastAPI — serves UI + API on the same port)
  ↓
OCI GenAI (layout compiler LLM)
  ↓
.drawio file   ←  download straight from the browser
```

---

## Accessing the UI

The server serves the web interface directly — **there is no separate front-end
process**. Once the server is running, open a browser and go to:

```
http://<instance-ip>:8080
```

That's it. The page lets you drag-and-drop a BOM.xlsx, optionally attach a
requirements file or paste context, then download the generated `.drawio` file.

---

## Running on OCI (Instance Principal)

The server uses **OCI Instance Principal** auth — no `~/.oci/config` needed.
The only secret you must supply is `SESSION_SECRET` (used to sign browser
session cookies).

### One-time setup: generate and store the session secret

```bash
# Generate a stable secret and save it to a file (mode 600)
openssl rand -hex 32 > ~/.drawing-agent-secret
chmod 600 ~/.drawing-agent-secret
```

> Store this file permanently — if it changes, all active browser sessions
> are invalidated and users must log in again.
>
> For higher security, store the secret in **OCI Vault** and fetch it at startup:
> ```bash
> SESSION_SECRET=$(oci secrets secret-bundle get \
>   --secret-id ocid1.vaultsecret.oc1... \
>   --auth instance_principal \
>   --query 'data."secret-bundle-content".content' \
>   --raw-output | base64 -d)
> ```

### Start the server (foreground)

```bash
cd ~/drawing-agent

SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080
```

### Start the server (background / persistent)

```bash
cd ~/drawing-agent

SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &

# Verify it started
sleep 3 && curl -s http://localhost:8080/health | python3 -m json.tool
```

### Restart after a code update

```bash
pkill -f uvicorn

SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &
```

---

## systemd Service (recommended for production)

Create `/etc/systemd/system/drawing-agent.service`:

```ini
[Unit]
Description=OCI Drawing Agent
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/drawing-agent
EnvironmentFile=/home/opc/.drawing-agent-secret.env
ExecStart=/usr/bin/python3.11 -m uvicorn drawing_agent_server:app \
    --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create `/home/opc/.drawing-agent-secret.env` (mode `600`):

```
SESSION_SECRET=<your-64-char-hex>
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now drawing-agent
sudo systemctl status drawing-agent
```

---

## Deploy updated files to OCI

```bash
scp drawing_agent_server.py index.html config.yaml opc@10.0.3.47:~/drawing-agent/
scp agent/bom_parser.py agent/layout_engine.py agent/drawio_generator.py \
    agent/oci_standards.py agent/layout_intent.py agent/intent_compiler.py \
    opc@10.0.3.47:~/drawing-agent/agent/

ssh opc@10.0.3.47 '
  pkill -f uvicorn
  SESSION_SECRET=$(cat ~/.drawing-agent-secret)
  cd ~/drawing-agent
  nohup python3.11 -m uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 \
    > agent.log 2>&1 &
  sleep 3
  curl -s http://localhost:8080/health
'
```

---

## Install dependencies

```bash
# Python 3.11+ required (OCI ADK incompatible with 3.9)
pip3.11 install -r requirements.txt
```

---

## Configuration (config.yaml)

| Section | Key | What it controls |
|---------|-----|-----------------|
| `region` | — | OCI region (e.g. `us-phoenix-1`) |
| `inference.enabled` | — | Use direct OCI GenAI Inference (true) vs legacy ADK (false) |
| `inference.model_id` | — | OCI GenAI model OCID |
| `inference.service_endpoint` | — | OCI GenAI endpoint URL |
| `compartment_id` | — | Compartment for GenAI calls |
| `auth.enabled` | — | Require OIDC login (default: `false`) |
| `auth.oidc_issuer` | — | OIDC issuer URL (e.g. Entra / Okta) |
| `auth.client_id` | — | OAuth2 client ID |
| `auth.redirect_uri` | — | Must match what's registered in the identity provider |
| `persistence.enabled` | — | Write diagrams to OCI Object Storage |

### Enabling OIDC login

Set `auth.enabled: true` in `config.yaml` and fill in the other `auth.*` fields.
Supply the client secret via the `OIDC_CLIENT_SECRET` env var:

```bash
SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
OIDC_CLIENT_SECRET=<your-secret> \
python3.11 -m uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080
```

When `auth.enabled: false` (default), all endpoints are open — suitable for
a private OCI subnet where network access is the only control.

---

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI (served directly by the server) |
| `POST` | `/upload-bom` | Upload BOM.xlsx → diagram or clarification questions |
| `POST` | `/clarify` | Submit answers to clarification questions |
| `POST` | `/generate` | Generate from a JSON resource list |
| `GET` | `/download/{file}` | Download generated `.drawio` file |
| `POST` | `/refresh-data` | Reload LLM runner in background (no restart needed) |
| `GET` | `/health` | Health check |
| `GET` | `/config` | UI configuration (region, model info) |
| `GET` | `/login` | Initiate OIDC login (only when auth enabled) |
| `GET` | `/logout` | Clear session |
| `GET` | `/.well-known/agent.json` | A2A agent card (machine-to-machine discovery) |
| `POST` | `/api/a2a/task` | A2A task endpoint (fleet integration) |
| `GET` | `/mcp/tools` | MCP tool manifest |

---

## API smoke tests

```bash
HOST=http://10.0.3.47:8080

# Health
curl -s $HOST/health | python3 -m json.tool

# Upload BOM
curl -X POST $HOST/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1" | python3 -m json.tool

# With requirements context
curl -X POST $HOST/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "context_file=@requirements.md" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1" | python3 -m json.tool

# Answer clarification questions (stateless — echo back _clarify_context fields)
curl -X POST $HOST/clarify \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "test1",
    "answers": "6 regions, active-passive HA",
    "diagram_name": "test_diagram",
    "items_json": "<value from need_clarification response>",
    "prompt":     "<value from need_clarification response>"
  }'

# Download diagram
curl -o test_diagram.drawio \
  "$HOST/download/diagram.drawio?client_id=test1&diagram_name=test_diagram"
```

---

## Run tests locally

```bash
pytest tests/ -v
```

---

## Repository structure

```
arch_assistant/
├── drawing_agent_server.py     # FastAPI server — UI + API in one process
├── index.html                  # Single-page web UI (served by the server)
├── config.yaml                 # Region, model, auth, persistence config
├── requirements.txt
├── Dockerfile
│
├── agent/
│   ├── bom_parser.py           # BOM → ServiceItem list + LLM prompt
│   ├── layout_engine.py        # Layout spec → x,y positions
│   ├── drawio_generator.py     # Positions → draw.io XML
│   ├── oci_standards.py        # OCI icon stencils (147KB)
│   ├── layout_intent.py        # LayoutIntent validation
│   ├── intent_compiler.py      # LayoutIntent → flat spec
│   └── persistence_objectstore.py
│
└── tests/
    ├── test_bom_parser.py
    ├── test_layout_engine.py
    ├── test_intent_compiler.py
    └── fixtures/
        └── sample_bom.xlsx
```

---

## OCI environment

| Setting | Value |
|---------|-------|
| Host | `opc@10.0.3.47` |
| Port | **8080** |
| Python | 3.11+ |
| Auth | Instance Principal (no config file needed) |
| Region | `us-phoenix-1` |

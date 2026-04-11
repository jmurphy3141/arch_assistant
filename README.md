# OCI Architecture Assistant (Agent 3 — v1.3.2)

Converts an Excel Bill of Materials (BOM) into a fully-editable draw.io OCI
architecture diagram, and generates POV and JEP documents from meeting notes.
Correct OCI icon stencils, VCN topology, subnets, and gateways — all in one
Python process with a dark-theme browser UI.

```
BOM.xlsx  →  drag-and-drop to OCI bucket  →  A2A generate  →  .drawio download
Notes     →  /notes/upload                →  /pov/generate  →  Markdown POV
                                          →  /jep/generate  →  Markdown JEP
```

---

## Accessing the UI

The server serves the React front-end directly on port 8080. In production an
nginx reverse proxy exposes it on port 443 (HTTPS). Open a browser and go to:

```
https://<instance-ip>
```

The page lets you:
- Drag-and-drop a BOM.xlsx (uploads to OCI bucket, then generates the diagram)
- Attach a requirements file or paste architecture context
- Fill in a 10-question architecture questionnaire (blank fields are inferred by the LLM)
- Download the generated `.drawio` file

---

## Building the UI

The React app lives in `ui/`. Build it once before deploying (or after any UI
changes):

```bash
cd ui
npm install
npm run build       # outputs to ui/dist/
```

The server automatically serves `ui/dist/` — no separate web server needed.

---

## Running on OCI (Instance Principal)

The server uses **OCI Instance Principal** auth — no `~/.oci/config` needed.
The only secret you must supply is `SESSION_SECRET`.

### One-time setup: session secret

```bash
openssl rand -hex 32 > ~/.drawing-agent-secret
chmod 600 ~/.drawing-agent-secret
```

### Start the server (background)

```bash
cd ~/drawing-agent

SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &

sleep 3 && curl -s http://localhost:8080/health
```

### Restart after a code update

```bash
cd ~/drawing-agent
git pull origin claude/webapp-fastapi-tests-sWH4S
pkill -f uvicorn

SESSION_SECRET=$(cat ~/.drawing-agent-secret) \
nohup python3.11 -m uvicorn drawing_agent_server:app \
  --host 0.0.0.0 --port 8080 > agent.log 2>&1 &

sleep 3 && curl -s http://localhost:8080/health
```

---

## systemd Service (recommended for production)

Create `/etc/systemd/system/oci-agent.service`:

```ini
[Unit]
Description=OCI Architecture Assistant
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/drawing-agent
EnvironmentFile=/home/opc/.drawing-agent.env
ExecStart=/usr/bin/python3.11 -m uvicorn drawing_agent_server:app \
    --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Create `/home/opc/.drawing-agent.env` (mode `600`):

```
SESSION_SECRET=<your-64-char-hex>
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oci-agent
sudo systemctl status oci-agent
journalctl -u oci-agent -f   # follow logs
```

---

## nginx Reverse Proxy (HTTPS on port 443)

```nginx
# /etc/nginx/conf.d/oci-agent.conf
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     /etc/ssl/certs/oci-agent.crt;
    ssl_certificate_key /etc/ssl/private/oci-agent.key;

    location / {
        proxy_pass         http://127.0.0.1:8080;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

SELinux (Oracle Linux 9): allow nginx to connect to the backend:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

OS firewall:

```bash
sudo firewall-cmd --add-service=https --permanent
sudo firewall-cmd --reload
```

---

## Install dependencies

```bash
# Python 3.11+ required (OCI ADK incompatible with 3.9)
pip3.11 install -r requirements.txt
```

---

## Configuration

### config.yaml — non-secret OCI settings

| Key | What it controls |
|-----|-----------------|
| `region` | OCI region (e.g. `us-chicago-1`) |
| `inference.enabled` | Use direct OCI GenAI Inference (true) vs legacy ADK (false) |
| `inference.model_id` | OCI GenAI model OCID |
| `inference.service_endpoint` | OCI GenAI endpoint URL |
| `compartment_id` | Compartment for GenAI calls |
| `persistence.enabled` | Write diagrams + docs to OCI Object Storage |
| `persistence.bucket_name` | OCI bucket name (default: `agent_assistante`) |
| `writing.max_tokens` | Token budget for POV/JEP generation |
| `writing.temperature` | Sampling temperature for document writing (default: 0.7) |

### .env — secrets and per-deployment values

Copy `.env.example` to `.env` and fill in the values. On OCI Compute set these
via systemd `EnvironmentFile` or OCI Vault instead.

| Variable | Required | Description |
|----------|----------|-------------|
| `SESSION_SECRET` | ✅ | Long random string for signing session cookies. Generate: `openssl rand -hex 32`. Keep stable across restarts. |
| `OIDC_CLIENT_ID` | for auth | Confidential app client ID from OCI Identity Domain |
| `OIDC_CLIENT_SECRET` | for auth | Confidential app client secret |
| `OIDC_AUTHORIZATION_ENDPOINT` | for auth | Identity Domain OAuth authorize URL |
| `OIDC_TOKEN_ENDPOINT` | for auth | Identity Domain OAuth token URL |
| `OIDC_USERINFO_ENDPOINT` | for auth | Identity Domain OIDC userinfo URL |
| `OIDC_REDIRECT_URI` | for auth | Callback URL registered in the Identity Domain app |
| `OIDC_LOGOUT_ENDPOINT` | optional | Identity Domain logout URL |
| `OIDC_REQUIRED_GROUP` | optional | Require membership in this Identity Domain group |

Auth is automatically enabled when the four required OIDC vars are set.
Leave them unset to run without authentication.

---

## API endpoints

### Diagram (Agent 3)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/upload-bom` | Upload BOM.xlsx → diagram or clarification questions |
| `POST` | `/clarify` | Submit answers to clarification questions |
| `POST` | `/generate` | Generate from a JSON resource list |
| `POST` | `/upload-to-bucket` | Upload a file to OCI Object Storage |
| `GET` | `/download/{file}` | Download generated `.drawio` file |
| `POST` | `/api/a2a/task` | A2A task endpoint (fleet integration) |

### Notes

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/notes/upload` | Upload a meeting notes file for a customer |
| `GET` | `/notes/{customer_id}` | List all notes for a customer |

### POV — Point of View document (Agent 4)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/pov/generate` | Generate or update a POV document from notes |
| `GET` | `/pov/{customer_id}/latest` | Retrieve the latest POV |
| `GET` | `/pov/{customer_id}/versions` | List all POV versions |

### JEP — Joint Execution Plan (Agent 5)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/jep/generate` | Generate or update a JEP from notes + diagram |
| `GET` | `/jep/{customer_id}/latest` | Retrieve the latest JEP |
| `GET` | `/jep/{customer_id}/versions` | List all JEP versions |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/config` | UI configuration (region, model info) |
| `POST` | `/refresh-data` | Reload LLM runner without restart |
| `GET` | `/.well-known/agent.json` | A2A agent card |
| `GET` | `/mcp/tools` | MCP tool manifest |

---

## API smoke tests

```bash
HOST=https://<instance-ip>

# Health
curl -sk $HOST/health | python3 -m json.tool

# Upload BOM directly (multipart)
curl -sk -X POST $HOST/api/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1" | python3 -m json.tool

# Upload a file to OCI bucket (step 1 of drag-and-drop flow)
curl -sk -X POST $HOST/api/upload-to-bucket \
  -F "customer_id=acme" \
  -F "file=@BOM.xlsx" | python3 -m json.tool

# Generate via A2A (bucket-side BOM — step 2 of drag-and-drop flow)
curl -sk -X POST $HOST/api/a2a/task \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-001",
    "skill": "upload_bom",
    "client_id": "acme",
    "inputs": {
      "bom_from_bucket": {
        "namespace": "oraclejamescalise",
        "bucket": "agent_assistante",
        "object": "agent3/acme/BOM.xlsx"
      },
      "diagram_name": "acme_architecture"
    }
  }' | python3 -m json.tool

# Upload meeting notes
curl -sk -X POST $HOST/api/notes/upload \
  -F "customer_id=acme" \
  -F "note_name=kickoff.md" \
  -F "file=@notes.md" | python3 -m json.tool

# Generate POV
curl -sk -X POST $HOST/api/pov/generate \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "acme", "customer_name": "ACME Corp"}' | python3 -m json.tool

# Generate JEP
curl -sk -X POST $HOST/api/jep/generate \
  -H "Content-Type: application/json" \
  -d '{"customer_id": "acme", "customer_name": "ACME Corp"}' | python3 -m json.tool

# Download diagram
curl -sk -o diagram.drawio \
  "$HOST/api/download/diagram.drawio?client_id=test1&diagram_name=test_diagram"
```

---

## OCI Object Storage layout

**Bucket**: `agent_assistante` | **Namespace**: `oraclejamescalise`

```
agent_assistante/
├── agent3/{client_id}/{diagram_name}/
│   ├── {request_id}/
│   │   ├── diagram.drawio
│   │   ├── spec.json
│   │   └── render_manifest.json
│   └── LATEST.json          ← atomic pointer to latest successful run
│
├── notes/{customer_id}/
│   ├── {note_name}          ← meeting notes (text/markdown)
│   └── MANIFEST.json
│
├── pov/{customer_id}/
│   ├── v1.md  v2.md  ...
│   ├── LATEST.md
│   └── MANIFEST.json
│
└── jep/{customer_id}/
    ├── v1.md  v2.md  ...
    ├── LATEST.md
    └── MANIFEST.json
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
├── drawing_agent_server.py     # FastAPI server — UI + all API endpoints
├── config.yaml                 # Region, model, persistence, writing-agent config
├── requirements.txt
├── Dockerfile
│
├── agent/
│   ├── bom_parser.py           # BOM → ServiceItem list + LLM prompt
│   ├── layout_engine.py        # Layout spec → x,y positions
│   ├── drawio_generator.py     # Positions → draw.io XML
│   ├── oci_standards.py        # OCI icon stencils (147KB)
│   ├── layout_intent.py        # LayoutIntent schema
│   ├── intent_compiler.py      # LayoutIntent → flat spec
│   ├── persistence_objectstore.py
│   ├── pov_agent.py            # Point of View document generator (Agent 4)
│   ├── jep_agent.py            # Joint Execution Plan generator (Agent 5)
│   ├── bom_stub.py             # Stub BOM extractor from meeting notes
│   ├── document_store.py       # Versioned doc storage (MANIFEST.json pattern)
│   └── context_store.py        # Shared notes/context tracker across agents
│
├── ui/                         # React + Vite front-end (dark OCI theme)
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts       # All API calls
│   │   └── components/
│   │       └── UploadBom.tsx   # Main drag-and-drop + generate form
│   ├── dist/                   # Built output — served by FastAPI
│   └── package.json
│
└── tests/
    ├── test_bom_parser.py
    ├── test_layout_engine.py
    ├── test_intent_compiler.py
    └── fixtures/
        └── sample_bom.xlsx
```

---

## Agent fleet

This server is **Agent 3** of a 7-agent OCI fleet. Agents 4 and 5 run in the
same process.

| # | Agent | Status | Endpoint |
|---|-------|--------|----------|
| 1 | Requirements gathering | planned | — |
| 2 | BOM sizing + pricing | planned | — |
| **3** | **Architecture diagram** | **this server** | `/upload-bom`, `/generate`, `/api/a2a/task` |
| **4** | **POV document** | **this server** | `/pov/generate` |
| **5** | **JEP document** | **this server** | `/jep/generate` |
| 6 | Terraform generation | planned | — |
| 7 | Well-Architected Framework review | planned | — |

---

## OCI environment

| Setting | Value |
|---------|-------|
| Host | `opc@10.0.3.47` |
| Internal port | **8080** |
| External port | **443** (nginx) |
| Python | 3.11+ |
| Auth | Instance Principal |
| Region | `us-phoenix-1` |

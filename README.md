# OCI Drawing Agent (Agent 3 v1.3.2)

Converts an Excel Bill of Materials (BOM) into a draw.io OCI architecture diagram.
Part of a 7-agent OCI fleet.

```
BOM.xlsx + optional context
  ↓
FastAPI server (server/)   ←──  OCI GenAI ADK (layout compiler)
  ↓
React SPA (ui/)            ←──  OCI Load Balancer + WAF
  ↓
.drawio artifact + JSON artefacts
```

---

## UI Quick Start

```bash
cd ui
cp .env.example .env          # VITE_API_BASE_URL=/api (default, keep as-is)
npm install
npm run dev                   # http://localhost:8080  (proxies /api → localhost:8000)
```

**Build (static files for deployment):**
```bash
npm run build                 # output: ui/dist/
```

**Run tests:**
```bash
npm test                      # vitest run (headless)
npm run test:watch            # interactive watch mode
```

---

## Server Quick Start

```bash
cd server
cp .env.example .env          # fill in ALLOWED_BUCKETS etc.
pip install -r requirements.txt
```

**Run (from repo root):**
```bash
uvicorn server.app.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips='*'
```

**Run tests:**
```bash
cd server
pytest -v
```

**Legacy server still works on port 8080 (unchanged):**
```bash
uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 --reload
```

---

## Single-VM Deployment Notes

The recommended deployment topology runs both services on **one OCI Compute VM**:

| Service | Port | Process |
|---------|------|---------|
| React SPA (static) | **8080** | `serve -s ui/dist -l 8080` or nginx |
| FastAPI API | **8000** | `uvicorn server.app.main:app --port 8000` |

An **OCI Load Balancer** (with optional WAF) sits in front and routes by path:

| Path pattern | Backend |
|-------------|---------|
| `/api/*`    | VM:8000 (FastAPI) |
| `/*`        | VM:8080 (React SPA) |

Because both are served via the same domain the UI calls the API using the
**relative path** `/api` — no CORS configuration needed.

### Process management (systemd example)

```ini
# /etc/systemd/system/oci-drawing-api.service
[Unit]
Description=OCI Drawing Agent API
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/arch_assistant
ExecStart=/home/opc/.venv/bin/uvicorn server.app.main:app \
    --host 127.0.0.1 --port 8000 \
    --proxy-headers --forwarded-allow-ips='*'
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/oci-drawing-ui.service
[Unit]
Description=OCI Drawing Agent UI
After=network.target

[Service]
User=opc
WorkingDirectory=/home/opc/arch_assistant/ui
ExecStart=/usr/bin/npx serve -s dist -l 8080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

---

## Curl Quick Start

```bash
HOST=http://localhost:8000   # or https://your-lb-host

# Health
curl -s $HOST/api/health | jq .

# Upload BOM
curl -X POST $HOST/api/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=my_arch" \
  -F "client_id=my-uuid" | jq .status

# Generate inline (JSON body)
curl -X POST $HOST/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "resources": [
      {"id":"lb_1","oci_type":"load balancer","label":"LB","layer":"ingress"},
      {"id":"compute_1","oci_type":"compute","label":"App","layer":"compute"},
      {"id":"db_1","oci_type":"database","label":"DB","layer":"data"}
    ],
    "diagram_name": "my_arch",
    "client_id": "my-uuid"
  }' | jq '{status,request_id,input_hash}'

# Generate bucket mode (resources.json in OCI Object Storage)
curl -X POST $HOST/api/generate \
  -H "Content-Type: application/json" \
  -d '{
    "resources_from_bucket": {"bucket":"my-bucket","object":"resources.json"},
    "diagram_name": "my_arch",
    "client_id": "my-uuid"
  }' | jq .status

# Clarify (after need_clarification response)
curl -X POST $HOST/api/clarify \
  -H "Content-Type: application/json" \
  -d '{"client_id":"my-uuid","diagram_name":"my_arch","answers":"Single region, active-passive HA"}' \
  | jq .status

# Download diagram
curl -o my_arch.drawio \
  "$HOST/api/download/diagram.drawio?client_id=my-uuid&diagram_name=my_arch"

# Validate bucket refs without generating
curl -X POST $HOST/api/inputs/resolve \
  -H "Content-Type: application/json" \
  -d '{"resources_from_bucket":{"bucket":"my-bucket","object":"resources.json"}}' \
  | jq .
```

---

## OCI LB / WAF Notes

### Load Balancer backend sets

| Backend set | Protocol | Port | Health-check path |
|-------------|----------|------|------------------|
| `api-backend` | HTTP | 8000 | `/api/health` |
| `ui-backend`  | HTTP | 8080 | `/` (200 OK from static) |

### Listener rules (path-based routing)

```
IF path begins with /api/  → route to api-backend
DEFAULT                    → route to ui-backend
```

### WAF recommended policies

| Policy | Value |
|--------|-------|
| Max request body size | 30 MB (covers BOM upload) |
| Rate limit | 100 req/min per IP (adjust to load) |
| Allow `/api/upload-bom` POST | body up to 25 MB |
| Protection rules | OWASP Core Ruleset (CRS) enabled |

### Backend set timeouts

The layout+LLM pipeline can take 30–60 s for complex BOMs.
Set backend **connection idle timeout** to **120 s** minimum.

### Security

- Instance Principal auth — no credentials stored anywhere.
- `ALLOWED_BUCKETS` env var enforces server-side bucket allowlist.
- UI never fetches from OCI directly; all bucket access is server-side.
- WAF handles TLS termination; backend uses plain HTTP on private network.

---

## Repository Structure

```
arch_assistant/
├── drawing_agent_server.py     # Legacy server (port 8080, backwards compat)
├── a2a_server.py               # A2A protocol server (port 8081)
├── mcp_server.py               # MCP stdio server
├── config.yaml                 # OCI endpoint IDs, region
├── Dockerfile
│
├── agent/                      # Core library (shared by both servers)
│   ├── bom_parser.py
│   ├── layout_engine.py
│   ├── drawio_generator.py
│   ├── oci_standards.py
│   ├── layout_intent.py
│   ├── intent_compiler.py
│   └── persistence_objectstore.py
│
├── server/                     # FastAPI server (port 8000, /api prefix)
│   ├── app/
│   │   └── main.py             # Full FastAPI app with bucket mode
│   ├── services/
│   │   └── oci_object_storage.py  # Mockable OCI bucket helper
│   ├── tests/
│   │   ├── conftest.py
│   │   └── test_api.py
│   ├── requirements.txt
│   ├── pytest.ini
│   └── .env.example
│
├── ui/                         # React + Vite + TypeScript SPA
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── api/client.ts       # All API calls
│   │   ├── agents/registry.ts  # Agent fleet registry
│   │   ├── flow/runner.ts      # Multi-agent flow abstraction
│   │   ├── hooks/
│   │   │   ├── useClientId.ts  # Stable UUID from localStorage
│   │   │   └── useHealth.ts    # Health polling
│   │   ├── components/
│   │   │   ├── HealthIndicator.tsx
│   │   │   ├── UploadBom.tsx
│   │   │   ├── GenerateForm.tsx
│   │   │   ├── ResponseDisplay.tsx
│   │   │   └── ClarifyForm.tsx
│   │   └── __tests__/
│   │       ├── setup.ts
│   │       ├── handlers.ts     # MSW request handlers
│   │       └── App.test.tsx
│   ├── package.json
│   ├── vite.config.ts
│   └── .env.example
│
├── tests/                      # Existing root-level tests
│   ├── test_bom_parser.py
│   ├── test_layout_engine.py
│   └── scenarios/
│
└── README.md

---

## OCI Object Storage — Artifact Persistence

Diagrams and JSON artefacts are persisted to OCI Object Storage when
`persistence.enabled: true` in `config.yaml`.

### Key layout in the bucket

```
{prefix}/{client_id}/{diagram_name}/{request_id}/diagram.drawio
{prefix}/{client_id}/{diagram_name}/{request_id}/spec.json
{prefix}/{client_id}/{diagram_name}/{request_id}/draw_dict.json
{prefix}/{client_id}/{diagram_name}/{request_id}/render_manifest.json
{prefix}/{client_id}/{diagram_name}/{request_id}/node_to_resource_map.json
{prefix}/{client_id}/{diagram_name}/LATEST.json          ← written last (atomic)
```

`store_inputs: false` (default) — raw BOM content is **never** stored.

### Required IAM policies

The OCI Compute instance must be in a dynamic group whose matching rule
covers Agent 3's instance OCID or compartment, e.g.:

```
Any {instance.compartment.id = 'ocid1.compartment.oc1..aaaa...'}
```

Grant the dynamic group the following policies **in the bucket's compartment**:

```hcl
# Allow Agent 3 to write artefacts
Allow dynamic-group <dg-name> to manage objects
  in compartment id ocid1.compartment.oc1..aaaaaaaam3ygxdq2vqr7djdhxo76uy6k6n523azgwdcei73wekc7u5v52lea
  where target.bucket.name = 'agent_assistante'

# Allow Agent 3 to read (download endpoint)
Allow dynamic-group <dg-name> to read objects
  in compartment id ocid1.compartment.oc1..aaaaaaaam3ygxdq2vqr7djdhxo76uy6k6n523azgwdcei73wekc7u5v52lea
  where target.bucket.name = 'agent_assistante'
```

Minimum required verbs: `manage objects` (for put/delete) and `read objects`
(for get/head). No bucket-level permissions are needed for this use case.

### Security note

Raw BOM files and context text are **not** persisted by default
(`store_inputs: false`). Only sanitised, agent-generated artefacts
(diagram XML + JSON) are written to Object Storage. Enable `store_inputs`
only after internal data-handling approval.

### Smoke test (on OCI Compute only)

```bash
python scripts/object_store_smoke.py
```

Writes and reads back a small test object; prints `OK` on success.
```

# Agent 3 — OCI Architecture Diagram Agent: Detailed Specification

## 1. Purpose

Accepts an Excel Bill of Materials (BOM) produced by Agent 2 (BOM Sizing) and
generates a fully-editable draw.io architecture diagram of the equivalent OCI
deployment. Uses official OCI icon stencils, correct subnet topology, and
outputs flat draw.io XML where every element is independently moveable.

---

## 2. System Context

This is **Agent 3** in a 7-agent OCI fleet:

| # | Agent | Status |
|---|-------|--------|
| 1 | Requirements gathering | planned |
| 2 | BOM sizing + pricing | live |
| **3** | **Architecture diagram** | **this spec** |
| 4 | Sizing validation | planned |
| 5 | Cost optimisation | planned |
| 6 | Terraform generation | live |
| 7 | Well-Architected Framework review | live |

**Upstream:** Receives `BOM.xlsx` from Agent 2 (or direct upload from user).
**Downstream:** Provides `.drawio` file to Agent 4 for validation; exposes
diagram XML to Agent 6 for Terraform generation.

---

## 3. Architecture Overview

```
Client (User / Agent 2 / Claude Desktop)
        │
        │  REST (port 8080) │ A2A (port 8081) │ MCP (stdio)
        ▼
┌───────────────────────────────────────────────────────┐
│  drawing_agent_server.py  (FastAPI)                   │
│                                                       │
│  POST /upload-bom                                     │
│  POST /clarify                                        │
│  POST /generate                                       │
│  POST /chat                                           │
│  GET  /download/{filename}                            │
│  GET  /health                                         │
│  GET  /mcp/tools                                      │
│  GET  /.well-known/agent-card.json                    │
└────────────────────────┬──────────────────────────────┘
                         │
           ┌─────────────▼──────────────┐
           │        agent/              │
           │  bom_parser.py             │  Excel → ServiceItem list + LLM prompt
           │  llm_client.py             │  OCI GenAI ADK call
           │  layout_engine.py          │  JSON spec → pixel positions
           │  drawio_generator.py       │  Positions → draw.io XML
           │  oci_standards.py          │  OCI icon stencil data
           └────────────────────────────┘
                         │
               OCI GenAI Agent Endpoint
               (Instance Principal auth)
```

**Supporting servers (same process / separate startup):**

| Server | File | Port | Protocol |
|--------|------|------|----------|
| Main REST | `drawing_agent_server.py` | 8080 | HTTP/JSON |
| A2A | `a2a_server.py` | 8081 | HTTP/JSON (A2A protocol) |
| MCP | `mcp_server.py` | stdio | JSON-RPC 2.0 |

---

## 4. Pipeline

```
BOM.xlsx  +  optional context (text or file)
    │
    ▼
bom_parser.parse_bom()
    • Reads BOM sheet (required)
    • Reads Input sheet (optional, for qty/OCPU context)
    • Deduplicates by oci_type (one icon per service type)
    • Injects BEST_PRACTICE services (gateways, WAF, monitoring, etc.)
    • Always prepends On-Premises element
    └── returns List[ServiceItem]

bom_parser.build_llm_prompt(items, context)
    • Builds structured prompt with:
        - DEFAULT ASSUMPTION TABLE (HA, DR, gateways, multi-region rules)
        - SERVICE LIST (from BOM)
        - LAYOUT RULES (tier model, placement, subnet naming)
        - OUTPUT JSON SCHEMA (hierarchical regions/ADs/FDs/subnets/nodes)
        - CLARIFICATION conditions (only truly blocking questions)
    └── returns str (prompt)

OCI GenAI Agent (via call_llm / llm_client)
    • Calls agent endpoint with Instance Principal auth
    • Multi-turn sessions keyed by client_id
    • Returns either:
        A. JSON layout spec (proceed to layout engine)
        B. {"status": "need_clarification", "questions": [...]}
    └── returns dict

layout_engine.spec_to_draw_dict(layout_spec, items_by_id)
    • Converts hierarchical spec → absolute pixel positions
    • Handles: single_ad / multi_ad / multi_region deployment types
    • Places region boxes, AD boxes, FD boxes (dashed), subnet boxes, icon nodes
    • Overrides gateway X positions to straddle VCN edges
    • Injects fixed standard edges (DRG→subnet, IGW→subnet, tier→tier, etc.)
    └── returns {"nodes": [...], "boxes": [...], "edges": [...]}

drawio_generator.generate_drawio(draw_dict, output_path)
    • Renders flat draw.io XML (all cells at parent="1")
    • Embeds multi-cell OCI icon stencils
    • Applies OCI Redwood colour palette per box type
    • Writes .drawio file
    └── returns Path
```

---

## 5. Data Models

### 5.1 ServiceItem

```python
@dataclass
class ServiceItem:
    id: str          # Unique ID, e.g. "compute_1", "on_prem"
    oci_type: str    # Canonical type, e.g. "compute", "database", "load balancer"
    label: str       # Human-readable label (may contain \n for multi-line)
    layer: str       # One of: external | ingress | compute | async | data
    quantity: float | None
    notes: str = ""
```

### 5.2 LLM Layout Spec (output from OCI GenAI)

```json
{
  "deployment_type": "single_ad | multi_ad | multi_region",
  "page": { "width": 1654, "height": 1169 },
  "regions": [
    {
      "id": "region_primary",
      "label": "OCI Region — us-phoenix-1",
      "regional_subnets": [
        {
          "id": "reg_sub_1",
          "tier": "ingress",
          "label": "Regional Ingress",
          "nodes": [{ "id": "waf_1", "oci_type": "waf", "label": "WAF" }]
        }
      ],
      "availability_domains": [
        {
          "id": "ad1",
          "label": "AD-1",
          "fault_domains": [
            {
              "id": "fd1",
              "label": "FD-1",
              "nodes": []
            }
          ],
          "subnets": [
            {
              "id": "pub_sub",
              "tier": "ingress",
              "label": "Public Subnet",
              "nodes": [
                { "id": "lb_1", "oci_type": "load balancer", "label": "Load Balancer" }
              ]
            }
          ]
        }
      ],
      "gateways": [
        { "id": "igw_1",  "oci_type": "internet gateway",  "label": "IGW" },
        { "id": "nat_1",  "oci_type": "nat gateway",       "label": "NAT" },
        { "id": "sgw_1",  "oci_type": "service gateway",   "label": "SGW" },
        { "id": "drg_1",  "oci_type": "drg",               "label": "DRG" }
      ],
      "oci_services": [
        { "id": "monitoring_1", "oci_type": "monitoring", "label": "Monitoring" }
      ]
    }
  ],
  "external": [
    { "id": "on_prem", "oci_type": "on premises", "label": "On-Premises" }
  ],
  "edges": [
    { "id": "e1", "source": "on_prem", "target": "drg_1", "label": "" }
  ]
}
```

### 5.3 Clarification Response

```json
{
  "status": "need_clarification",
  "questions": [
    "How many regions is this deployment spanning?",
    "Is there a requirement for Disaster Recovery?"
  ]
}
```

### 5.4 Draw Dict (internal, output of layout_engine)

```python
{
  "nodes": [
    {
      "id": "compute_1",
      "label": "Compute\n×16 OCPU",
      "oci_type": "compute",
      "x": 480, "y": 320, "w": 48, "h": 48
    }
  ],
  "boxes": [
    {
      "id": "pub_sub",
      "label": "Public Subnet",
      "box_type": "_subnet_ingress",
      "tier": "ingress",
      "x": 200, "y": 180, "w": 320, "h": 120
    }
  ],
  "edges": [
    {
      "id": "e1",
      "source": "on_prem",
      "target": "drg_1",
      "label": "",
      "exitX": 1, "exitY": 0.5,
      "entryX": 0, "entryY": 0.5
    }
  ]
}
```

---

## 6. API Reference

### 6.1 REST Endpoints (port 8080)

#### `POST /upload-bom`

Upload an Excel BOM to generate a diagram.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | file | yes | Excel `.xlsx` file with `BOM` sheet |
| `diagram_name` | string | no | Output filename stem (default: `diagram`) |
| `client_id` | string | no | Client session ID (auto-generated if omitted) |
| `context` | string | no | Free-text requirements context |
| `context_file` | file | no | Requirements file (`.md`, `.txt`) |

**Response 200 — Diagram generated:**
```json
{
  "status": "ok",
  "client_id": "abc123",
  "diagram_name": "test_diagram",
  "drawio_xml": "<mxGraphModel ...>...</mxGraphModel>",
  "spec": { "deployment_type": "multi_ad", "regions": [...] },
  "download_url": "/download/test_diagram.drawio"
}
```

**Response 200 — Clarification needed:**
```json
{
  "status": "need_clarification",
  "client_id": "abc123",
  "questions": [
    "How many availability domains should be used?"
  ]
}
```

**Response 422** — BOM parse error or invalid file.
**Response 500** — LLM or generation failure.

---

#### `POST /clarify`

Submit answers to pending clarification questions.

**Request:** `application/json`
```json
{
  "answers": "3 ADs, active-active HA across all three",
  "client_id": "abc123",
  "diagram_name": "test_diagram"
}
```

**Response:** Same shape as `/upload-bom` (diagram or more questions).

---

#### `POST /generate`

Generate a diagram from a pre-parsed resource list (bypasses BOM parsing).

**Request:** `application/json`
```json
{
  "resources": [
    { "id": "compute_1", "oci_type": "compute", "label": "Compute", "layer": "compute", "quantity": 4 }
  ],
  "context": "HA deployment, 2 ADs",
  "diagram_name": "generated_diagram",
  "client_id": "abc123"
}
```

**Response:** Same shape as `/upload-bom`.

---

#### `POST /chat`

Free-form chat with the OCI GenAI agent (no diagram generation).

**Request:** `application/json`
```json
{
  "message": "What subnet topology should I use for a 3-tier app?",
  "client_id": "abc123"
}
```

**Response:**
```json
{
  "response": "For a 3-tier application on OCI, the recommended pattern is..."
}
```

---

#### `GET /download/{filename}`

Download a previously generated `.drawio` file.

**Response:** File attachment (`application/octet-stream`).
**Response 404** — File not found.

---

#### `GET /health`

Service health check.

**Response:**
```json
{
  "status": "ok",
  "agent": "ready",
  "pending_clarifications": 2
}
```

---

#### `GET /mcp/tools`

Returns MCP tool definitions (also used by Claude Desktop integration).

---

#### `GET /.well-known/agent-card.json`

A2A agent card — describes agent capabilities for discovery.

```json
{
  "name": "OCI Architecture Diagram Agent",
  "version": "1.0.0",
  "description": "Generates draw.io architecture diagrams from OCI BOM spreadsheets",
  "skills": [
    {
      "id": "generate_diagram",
      "name": "Generate OCI Architecture Diagram",
      "description": "Takes a list of OCI resources and produces a draw.io diagram",
      "inputSchema": { ... }
    }
  ]
}
```

---

### 6.2 A2A Endpoint (port 8081)

#### `POST /a2a/task`

**Request:**
```json
{
  "task_id": "task_001",
  "skill": "generate_diagram",
  "inputs": {
    "resources": [...],
    "context": "HA, 3 ADs",
    "diagram_name": "fleet_diagram"
  },
  "client_id": "agent2_session"
}
```

**Response:**
```json
{
  "task_id": "task_001",
  "status": "ok",
  "outputs": {
    "status": "ok",
    "drawio_xml": "...",
    "download_url": "/download/fleet_diagram.drawio"
  }
}
```

---

### 6.3 MCP Tools (stdio / JSON-RPC 2.0)

| Tool | Inputs | Description |
|------|--------|-------------|
| `upload_bom` | `bom_path`, `context?`, `diagram_name?`, `client_id?` | Parse BOM file and generate diagram |
| `generate_diagram` | `resources`, `context?`, `diagram_name?`, `client_id?` | Generate from resource list |
| `clarify` | `answers`, `client_id`, `diagram_name?` | Submit clarification answers |
| `get_oci_catalogue` | — | Return all known OCI resource types |

MCP message protocol: JSON-RPC 2.0 over stdin/stdout.

---

## 7. Configuration

**File:** `config.yaml`

```yaml
region: "us-phoenix-1"
agent_endpoint_id: "ocid1.genaiagentendpoint.oc1.phx.amaaaaaaqx2yg4ya76r5ojh7olsu5uxpe4bmmslfrpylfcrwezgms5azbppa"
compartment_id: "ocid1.compartment.oc1..."
max_steps: 5
host: "0.0.0.0"
port: 8080
output_dir: "/tmp/diagrams"
```

All values are non-secret OCI resource identifiers. No credentials are stored
here. Auth is exclusively via OCI Instance Principal.

---

## 8. Authentication & Security

- **Auth method:** OCI Instance Principal only.
- **No `~/.oci/config`** — the server must run on OCI Compute with an instance
  principal attached to the correct dynamic group and policy.
- **No hardcoded credentials** anywhere in the codebase.
- **Config values** (endpoint IDs, compartment ID) are non-secret and live in
  `config.yaml`.
- **Required IAM policy:**
  ```
  allow dynamic-group <dg-name> to use generative-ai-agent-endpoints
      in compartment <compartment-name>
  ```

---

## 9. Layout Rules

### 9.1 Page

- **Size:** A3 landscape — 1654 × 1169 px
- **Single region bounds:** x=144, y=120, w=1366

### 9.2 Icon sizing

- **Target icon size:** 48 × 48 px
- **Icon slot (icon + label + padding):** 76 px

### 9.3 Tier model (top → bottom)

| Tier | Content | Style |
|------|---------|-------|
| `ingress` | Load balancers, WAF, Firewall | White, solid border, orange label |
| `web` | Web servers, container instances | Dashed, light fill |
| `compute` | App servers, functions | Dashed, light fill |
| `async` | Queues, streaming | Dashed, light fill |
| `data` | Databases, object storage, vault | Dashed, blue-tint fill |

### 9.4 Gateway positioning (relative to VCN edges)

| Gateway | Placement |
|---------|-----------|
| IGW (Internet Gateway) | Top centre of region |
| DRG | Left edge of region, 40% down |
| NAT | Right edge of region, 30% down |
| SGW (Service Gateway) | Right edge of region, below NAT |

### 9.5 Standard fixed edges (always injected)

| Source | Target | Description |
|--------|--------|-------------|
| DRG | Private ingress subnet | On-prem connectivity |
| IGW | Public ingress subnet | Internet ingress |
| Public ingress subnet | Web tier subnet | Forward traffic |
| Web tier | App tier | Internal traffic |
| App tier | DB tier | Data access |
| NAT | Internet | Outbound from private |
| SGW | OCI services box | OCI service access |

### 9.6 Multi-region

Two regions placed side by side with a gap. Each region is laid out
independently using the same rules. External elements (On-Premises) go in
the left column.

---

## 10. OCI Icons

Icons are sourced from `agent/oci_standards.py`, which contains compressed
multi-cell draw.io XML extracted from OCI Library v24.2. Each icon is:
- A group wrapper cell (`{id}_g`)
- One or more sub-cells (`{id}_s0`, `{id}_s1`, ...) rendered as stencil shapes

Mapping from `oci_type` → icon title is defined in `oci_standards.get_icon_title()`.

**Example mappings:**

| oci_type | Icon title |
|----------|-----------|
| `compute` | `Compute - Bare Metal Compute` |
| `database` | `Database - Oracle DB System` |
| `load balancer` | `Networking - Load Balancer` |
| `object storage` | `Storage - Object Storage` |
| `vault` | `Security - Vault` |
| `waf` | `Edge Services - Web Application Firewall` |
| `drg` | `Networking - Dynamic Routing Gateway` |
| `internet gateway` | `Networking - Internet Gateway` |
| `nat gateway` | `Networking - NAT Gateway` |
| `service gateway` | `Networking - Service Gateway` |

---

## 11. BOM Parsing

### 11.1 Required Excel structure

**Sheet: `BOM`** (required)

| Column | Content |
|--------|---------|
| A | SKU code (e.g. `B94176`) |
| B | Service description |
| C | Quantity |

**Sheet: `Input`** (optional)

| Column | Content |
|--------|---------|
| A | Parameter name |
| B | Value |

Parameters read from `Input`: `App OCPU`, `DB OCPU`, `Object Storage (GB)`.

### 11.2 SKU to OCI type mapping

| SKU | OCI Type | Layer |
|-----|----------|-------|
| B94176 | compute | compute |
| B99060 | database | data |
| B93030 | load balancer | ingress |
| *(13 more)* | … | … |

If SKU is not in the map, a fallback keyword match is applied against the
description.

### 11.3 Always-injected best-practice services

These are added regardless of what is in the BOM:

**Gateways:** internet gateway, nat gateway, service gateway, drg
**Security:** waf, network firewall
**Operations:** logging, monitoring, database management, identity directory, certificates

---

## 12. Clarification Logic

The LLM applies the **assumption-first** strategy: it never asks about things
it can infer from the BOM or from standard OCI best practices.

**Only ask if truly unknown and blocking:**
- Number of regions (only if multi-region hints present but count unclear)
- DR requirement (only if no regional redundancy cues in BOM)
- Network connectivity (CPE/VPN details only if on-prem is in BOM but method is unknown)

**Never ask about:**
- Whether to include gateways (always injected)
- Standard HA topology for a given service
- Best-practice subnets for a given tier
- Icon or visual preferences

The server stores clarification state per `client_id` in `PENDING_CLARIFY`.
Answers are appended to the original prompt and the full pipeline is re-run.

---

## 13. State Management

All state is in-process (no external store):

| Variable | Type | Purpose |
|----------|------|---------|
| `SESSION_STORE` | `dict[str, str]` | `client_id → session_id` for multi-turn LLM |
| `PENDING_CLARIFY` | `dict[str, dict]` | Stores `{items, prompt, diagram_name}` while awaiting answers |

State is **not persisted** across server restarts.

---

## 14. draw.io XML Structure

All cells are emitted at `parent="1"` (root canvas layer). This is intentional:
every element is independently draggable; no accidental group moves.

```xml
<mxGraphModel pageWidth="1654" pageHeight="1169" dx="0" dy="0" grid="0"
              tooltips="1" connect="1" arrows="1" fold="1" page="1"
              pageScale="1" math="0" shadow="0">
  <root>
    <mxCell id="0"/>                        <!-- Root node -->
    <mxCell id="1" parent="0"/>             <!-- Default parent (canvas) -->

    <!-- Region box -->
    <mxCell id="region_primary" value="OCI Region" vertex="1" parent="1"
            style="...">
      <mxGeometry x="144" y="120" width="1366" height="929" as="geometry"/>
    </mxCell>

    <!-- Icon group -->
    <mxCell id="compute_1_g" value="Compute&#xa;×16 OCPU" vertex="1" parent="1"
            style="group;...">
      <mxGeometry x="480" y="320" width="48" height="68" as="geometry"/>
      <!-- Sub-cells (stencil shapes) -->
      <mxCell id="compute_1_s0" vertex="1" parent="compute_1_g" style="shape=..."/>
      <mxCell id="compute_1_s1" vertex="1" parent="compute_1_g" style="shape=..."/>
    </mxCell>

    <!-- Edge -->
    <mxCell id="e_igw_pub" edge="1" source="igw_1_g" target="pub_sub" parent="1"
            style="edgeStyle=orthogonalEdgeStyle;...">
      <mxGeometry relative="1" as="geometry"/>
    </mxCell>
  </root>
</mxGraphModel>
```

---

## 15. OCI Redwood Colour Palette (Box Styles)

| Box type | Fill | Stroke | Label colour |
|----------|------|--------|--------------|
| Region | `#F5F4F2` | `#706A62` | `#312D2A` |
| Availability Domain | `#EDECE9` | `#9E9890` | `#312D2A` |
| Fault Domain | `transparent` | `#9E9890` dashed | `#706A62` |
| Subnet — ingress | `#FFFFFF` | `#CF4500` | `#CF4500` bold |
| Subnet — web/compute | `#FAFAF9` | `#9E9890` dashed | `#312D2A` |
| Subnet — data | `#F0F4FF` | `#4A5568` dashed | `#312D2A` |

---

## 16. Dependencies

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.0.0
python-multipart>=0.0.9
openpyxl>=3.1.0
pyyaml>=6.0
oci[adk]==2.165.1
```

**Runtime environment:**
- Python 3.11
- OCI Compute instance with Instance Principal
- Region: `us-phoenix-1`
- Optional: draw.io CLI v24.2.5 (PNG export only)

---

## 17. Deployment

### Docker
```bash
docker build -t oci-drawing-agent .
docker run -p 8080:8080 oci-drawing-agent
```
*(Instance Principal auth requires running on OCI Compute)*

### Direct (OCI Compute)
```bash
uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 --reload
```

### OCI Compute host
- **Host:** `opc@10.0.3.47`
- **Port:** 8080
- **Log:** `~/drawing-agent/agent.log`

---

## 18. Testing

```bash
pytest tests/ -v
```

**Test modules:**

| Module | Coverage |
|--------|----------|
| `tests/test_bom_parser.py` | LLM prompt generation, BOM parsing, best-practice injection |
| `tests/test_layout_engine.py` | Position computation, draw dict structure, fixed edges |

**Fixtures:**
- `tests/fixtures/sample_bom.xlsx` — required for BOM parse tests (not in repo; add manually)

---

## 19. Known Issues & Planned Enhancements

| # | Issue | Priority |
|---|-------|----------|
| 1 | `/generate` endpoint has broken `__wrapped__` attribute access (line ~305) | High |
| 2 | `diagram_orchestrator.py` is deprecated; remove once pipeline confirmed stable | Medium |
| 3 | Multi-region layout currently generates single representative region | Medium |
| 4 | PNG export not integrated into main response (requires draw.io CLI) | Low |
| 5 | Multiple clarification rounds supported but not tested end-to-end | Medium |
| 6 | `sample_bom.xlsx` test fixture missing from repo | Low |
| 7 | No external state persistence — server restart loses all session state | Low |

---

## 20. Integration Examples

### Agent 2 → Agent 3 (A2A)
```python
import httpx

response = httpx.post("http://10.0.3.47:8081/a2a/task", json={
    "task_id": "bom_to_diagram_001",
    "skill": "generate_diagram",
    "inputs": {
        "resources": bom_agent_resources,
        "context": "Active-passive HA, 2 regions",
        "diagram_name": "customer_arch"
    },
    "client_id": "agent2"
})
diagram_xml = response.json()["outputs"]["drawio_xml"]
```

### Claude Desktop / Claude Code (MCP)
Add to MCP config:
```json
{
  "mcpServers": {
    "oci-drawing-agent": {
      "command": "python",
      "args": ["/path/to/mcp_server.py"]
    }
  }
}
```
Then use tools: `upload_bom`, `generate_diagram`, `clarify`, `get_oci_catalogue`.

### Direct REST (curl)
```bash
# Upload BOM with context
curl -X POST http://10.0.3.47:8080/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "context=HA active-passive, 6 regions" \
  -F "diagram_name=customer_arch" \
  -F "client_id=session1"

# Answer clarification questions
curl -X POST http://10.0.3.47:8080/clarify \
  -H "Content-Type: application/json" \
  -d '{"client_id": "session1", "answers": "2 ADs, active-active", "diagram_name": "customer_arch"}'

# Download result
curl -O http://10.0.3.47:8080/download/customer_arch.drawio
```

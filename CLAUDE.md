# OCI Drawing Agent — Claude Code Guide

## What This Project Does

Takes an Excel Bill of Materials (BOM) from a cloud sizing agent and produces
a draw.io architecture diagram of the equivalent OCI deployment. Uses official
OCI icon stencils, correct subnet topology, and outputs fully-editable draw.io
XML.

**Input:** `BOM.xlsx` + optional requirements notes file
**Output:** `.drawio` file (flat structure, all cells at root, independently moveable)

---

## Repository Structure

```
oci-drawing-agent/
├── drawing_agent_server.py     # FastAPI server — main entry point
├── a2a_server.py               # A2A protocol server (port 8081)
├── mcp_server.py               # MCP stdio server
├── requirements.txt
├── config.yaml                 # Region, endpoint IDs, server config
├── Dockerfile
│
├── agent/
│   ├── __init__.py             # Exports: parse_bom, spec_to_draw_dict, generate_drawio
│   ├── bom_parser.py           # BOM → service list + LLM prompt
│   ├── layout_engine.py        # Layout spec → x,y positions
│   ├── drawio_generator.py     # Positions → draw.io XML
│   ├── oci_standards.py        # OCI icon stencil data (147KB)
│   ├── llm_client.py           # OCI GenAI ADK client (standalone use)
│   ├── diagram_orchestrator.py # DEPRECATED — keep for reference
│   └── png_exporter.py         # draw.io CLI → PNG (requires CLI installed)
│
├── tests/
│   ├── test_bom_parser.py
│   ├── test_layout_engine.py
│   └── fixtures/
│       └── sample_bom.xlsx     # Add to enable parse_bom tests
│
└── docs/
    └── pipeline.md             # Full pipeline reference
```

---

## Pipeline

```
BOM.xlsx + optional context file
  │
  ▼
bom_parser.py    SKU/desc lookup → ServiceItem list + LLM prompt
  │
  ▼
OCI GenAI        Layout compiler → layout spec JSON
  │              (or clarification questions)
  ▼
layout_engine.py Spec → deterministic x,y positions
  │              Computes VCN box, subnet boxes, gateway X overrides
  ▼
drawio_generator.py  Positions → flat draw.io XML (all parent="1")
```

---

## Auth

**OCI Instance Principal only.** No `~/.oci/config`. The server must run on
OCI Compute with an instance principal attached to the correct dynamic group
and policy.

Never hardcode credentials. Config values (endpoint IDs, compartment ID) live
in `config.yaml` — these are non-secret OCI resource identifiers.

---

## Development Commands

### Run the server locally (requires OCI auth)
```bash
uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 --reload
```

### Run tests
```bash
pytest tests/ -v
```

### Test pipeline without server (no OCI needed)
```python
from agent.bom_parser import bom_to_llm_input
from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio

items, prompt = bom_to_llm_input("BOM.xlsx", context="6 regions, HA active-passive")

# Hand-craft or mock the layout spec instead of calling the LLM:
mock_spec = {
    "direction": "LR",
    "page": {"width": 1654, "height": 1169},
    "layers": {
        "external": [{"id": "on_prem", "type": "on premises", "label": "On-Premises"}],
        "ingress":  [{"id": "drg_1",   "type": "drg",         "label": "DRG"}],
        "compute":  [{"id": "compute_1","type": "compute",     "label": "Compute"}],
        "async":    [],
        "data":     [{"id": "db_1",    "type": "database",     "label": "PostgreSQL DB"}],
    },
    "groups": [
        {"id": "pub_sub_box", "label": "Public Subnet",       "nodes": ["drg_1"]},
        {"id": "app_sub_box", "label": "App Subnet",          "nodes": ["compute_1"]},
        {"id": "db_sub_box",  "label": "DB Subnet",           "nodes": ["db_1"]},
        {"id": "region_box",  "label": "OCI Region Services", "nodes": []},
    ],
    "edges": [],
}

draw_dict = spec_to_draw_dict(mock_spec, {i.id: i for i in items})
generate_drawio(draw_dict, "output.drawio")
```

### Deploy to OCI Compute
```bash
scp drawing_agent_server.py opc@10.0.3.47:~/drawing-agent/
scp agent/bom_parser.py agent/layout_engine.py agent/drawio_generator.py \
    agent/oci_standards.py opc@10.0.3.47:~/drawing-agent/agent/

ssh opc@10.0.3.47 '
  pkill -f uvicorn
  cd ~/drawing-agent
  nohup uvicorn drawing_agent_server:app --host 0.0.0.0 --port 8080 > agent.log 2>&1 &
  sleep 3
  curl -s http://localhost:8080/health
'
```

### API smoke tests
```bash
# Full BOM upload
curl -X POST http://10.0.3.47:8080/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1"

# With requirements context file
curl -X POST http://10.0.3.47:8080/upload-bom \
  -F "file=@BOM.xlsx" \
  -F "context_file=@requirements.md" \
  -F "diagram_name=test_diagram" \
  -F "client_id=test1"

# Answer clarification questions
curl -X POST http://10.0.3.47:8080/clarify \
  -H "Content-Type: application/json" \
  -d '{"client_id": "test1", "answers": "6 regions, active-passive HA", "diagram_name": "test_diagram"}'
```

---

## Key Design Decisions

### Flat draw.io XML
Every cell is emitted at `parent="1"` (root). Icons sit visually inside
subnet boxes but are **not** children. This makes every element independently
draggable — no accidental group moves.

### OCI Icons
`agent/oci_standards.py` contains compressed multi-cell icon XML extracted
from `OCI_Library.xml` (Oracle draw.io stencil library v24.2). Each icon is
a wrapper group with sub-cells rendered as stencil shapes.

### Gateway X positioning
After computing subnet group bounding boxes, the layout engine overrides
gateway icon X positions to straddle VCN edges:
- IGW, NAT, DRG: `x = vcn_left - icon_w/2`
- SGW: `x = vcn_right - icon_w/2`

### LLM clarification flow
If the LLM returns `{"status": "need_clarification", "questions": [...]}`,
the server stores state in `PENDING_CLARIFY[client_id]` and returns the
questions to the caller. The caller POSTs answers to `/clarify`, which
appends them to the original prompt and re-runs the pipeline.

---

## Known Issues / Next Steps

1. **Config hardcoding** — `AGENT_ENDPOINT_ID`, `COMPARTMENT_ID`, `REGION`
   are still hardcoded in `drawing_agent_server.py`. Task: read from `config.yaml`.

2. **`diagram_orchestrator.py`** — deprecated, marked with `DeprecationWarning`.
   Remove once new pipeline is confirmed stable.

3. **PNG export** — `png_exporter.py` works but requires draw.io CLI (installed
   by Dockerfile). The `/upload-bom` response omits PNG by default — can be
   re-added.

4. **Multi-region** — currently generates a single representative region.
   6-region layout is a planned enhancement.

5. **Multiple clarification rounds** — `/clarify` supports one round. Multiple
   rounds work but are not tested end-to-end.

---

## OCI Environment

| Setting | Value |
|---------|-------|
| Host | `opc@10.0.3.47` |
| Port | 8080 |
| Python | 3.11 |
| OCI SDK | `oci[adk]==2.165.1` |
| Auth | Instance Principal |
| Region | `us-phoenix-1` |

---

## Agent Fleet Context

This is **Agent 3** of a planned 7-agent OCI fleet:

| # | Agent | Status |
|---|-------|--------|
| 1 | Requirements gathering | planned |
| 2 | BOM sizing + pricing | planned |
| **3** | **Architecture diagram** | **this project** |
| 4 | Sizing validation | planned |
| 5 | Cost optimisation | planned |
| 6 | Terraform generation | planned |
| 7 | Well-Architected Framework review | planned |

Agent-to-agent communication uses A2A protocol via `a2a_server.py` (port 8081).
MCP tool exposure via `mcp_server.py` (stdio).

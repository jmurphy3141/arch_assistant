# OCI Drawing Agent — Pipeline Reference

## Overview

```
BOM.xlsx + optional context file
  │
  ▼
bom_parser.py          Rule-based: Excel SKUs → ServiceItem list + LLM prompt
  │
  ▼
OCI GenAI Agent        Layout compiler: prompt → layout spec JSON
  │                    (or clarification questions if info missing)
  ▼
layout_engine.py       Spec → deterministic x,y positions per layer
  │                    Computes VCN box, group boxes, gateway edge positions
  ▼
drawio_generator.py    Positions → flat draw.io XML
                       All cells parent="1" (root) — nothing nested
```

## Modules

### `agent/bom_parser.py`

Parses an Excel BOM using `openpyxl`. Maps SKUs and description keywords to
OCI service types, then injects best-practice services (gateways, WAF,
logging, monitoring, etc.) regardless of BOM content.

Key functions:
- `parse_bom(xlsx_path) → list[ServiceItem]`
- `build_llm_prompt(items, context="") → str`
- `bom_to_llm_input(xlsx_path, context="") → (items, prompt)`

### `agent/layout_engine.py`

Takes the LLM's layout spec JSON and computes absolute pixel positions for
every node and group box on the A3 landscape canvas (1654 × 1169 px).

Layout is **deterministic** — no LLM creativity here. Rules:

| Concern | Rule |
|---------|------|
| Direction | Left → right |
| Layer order | external → ingress → compute → async → data |
| Icon size | 48 × 48 px, label below |
| Gateway X | Straddle VCN border (IGW/NAT/DRG left, SGW right) |
| VCN box | Computed bounding rect of three subnet boxes |

Key functions:
- `compute_positions(layout_spec) → (nodes, groups)`
- `spec_to_draw_dict(layout_spec, items_by_id) → draw_dict`

### `agent/drawio_generator.py`

Converts the positioned draw dict into draw.io XML. All cells are emitted
at `parent="1"` (root) — **flat structure**, nothing nested. Icons sit
visually inside subnet boxes but are not children, so every element is
independently draggable in draw.io.

Key design decisions:
1. **Flat structure** — `parent="1"` for everything
2. **Icon wrappers** — invisible (`fillColor=none;strokeColor=none`) but a
   real routing obstacle for draw.io's orthogonal edge router
3. **Icon sub-cells** — `connectable="0"` so inner stencil parts don't
   intercept edges
4. **Group boxes drawn first** — behind icons in z-order

Key function:
- `generate_drawio(draw_dict, output_path) → Path`

---

## LLM Layout Spec Format

The layout compiler (OCI GenAI Agent) must return one of two JSON shapes:

### Success — layout spec

```json
{
  "direction": "LR",
  "page": {"width": 1654, "height": 1169},
  "layers": {
    "external": [{"id": "on_prem", "type": "on premises", "label": "On-Premises"}],
    "ingress":  [{"id": "drg_1",   "type": "drg",         "label": "DRG / FastConnect"}],
    "compute":  [{"id": "compute_1","type": "compute",     "label": "Compute ×3,821 OCPU"}],
    "async":    [{"id": "queue_1", "type": "queue",        "label": "Queue"}],
    "data":     [{"id": "db_1",    "type": "database",     "label": "PostgreSQL DB"}]
  },
  "groups": [
    {"id": "pub_sub_box", "label": "Public Subnet",       "nodes": ["drg_1"]},
    {"id": "app_sub_box", "label": "App Subnet",          "nodes": ["compute_1"]},
    {"id": "db_sub_box",  "label": "DB Subnet",           "nodes": ["db_1"]},
    {"id": "region_box",  "label": "OCI Region Services", "nodes": []}
  ],
  "edges": [
    {"id": "e1", "source": "on_prem", "target": "drg_1", "label": "FastConnect ×6"}
  ]
}
```

### Clarification needed

```json
{
  "status": "need_clarification",
  "questions": ["How many regions?", "Active-active or active-passive HA?"]
}
```

---

## Fixed Edges

The layout engine always inserts these edges (not from the LLM):

| Source | Target | Label |
|--------|--------|-------|
| on_prem | vcn_box | FastConnect ×6 |
| internet_gateway | vcn_box | Internet |
| pub_sub_box | app_sub_box | LB Traffic |
| app_sub_box | db_sub_box | Data Access |
| vcn_box | region_box | (SGW, blank) |

Arrow entry/exit Y coordinates are computed dynamically to align with the
corresponding gateway icon on the VCN border.

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/upload-bom` | Upload BOM Excel + optional context file |
| POST | `/clarify` | Submit answers to clarification questions |
| POST | `/generate` | JSON body with pre-parsed resources list |
| POST | `/chat` | Free-form chat with the agent |
| GET | `/download/{filename}` | Download generated file from `/tmp/diagrams/` |
| GET | `/health` | Health check + pending clarifications |
| GET | `/mcp/tools` | MCP tool manifest |
| GET | `/.well-known/agent-card.json` | A2A agent card |

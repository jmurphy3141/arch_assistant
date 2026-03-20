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
  │                    Applies best-practice assumptions for any missing info
  │                    (or clarification questions only for blocking unknowns)
  ▼
layout_engine.py       Spec → deterministic x,y positions per tier (TB layout)
  │                    Computes Region, AD, FD, Subnet boxes; gateway positions
  ▼
drawio_generator.py    Positions → flat draw.io XML
                       All cells parent="1" (root) — nothing nested
```

---

## Assumption-First Design Principle

**The agent will almost never have perfect information.** A BOM tells us
what services exist; it rarely tells us the HA pattern, number of regions,
or fault-domain strategy. The agent must never stall on missing information
that can be reasonably inferred.

### Decision hierarchy

1. **Use explicit input** — anything stated in the BOM, context file, or
   clarification answers takes priority.
2. **Apply best-practice defaults** — if a decision is not stated, pick the
   OCI-recommended default for the workload class (see table below).
3. **Ask only for blocking unknowns** — information that would materially
   change the topology and cannot be safely assumed (e.g. number of regions
   for a DR requirement explicitly mentioned but not quantified).

### Default assumptions table

| Signal in BOM / context | Assumed topology |
|-------------------------|-----------------|
| No HA signal at all | Single AD, single FD (simplest correct deployment) |
| "HA" or redundancy mentioned, no detail | Single AD, two Fault Domains |
| Two ADs mentioned, or "regional HA" | Multi-AD active-passive |
| "DR", "multi-region", or two regions mentioned | Multi-region active-passive |
| "active-active" explicit | Single AD, two Fault Domains (active-active) |
| Database in BOM | Add Data Guard (sync for multi-AD, async for multi-region) |
| Any compute in BOM | Add NAT Gateway |
| Any OCI managed service (Object Storage, ATP, etc.) | Add Service Gateway |
| External users / HTTPS in BOM or context | Add IGW + WAF + Public Load Balancer |
| On-prem / VPN / FastConnect in BOM | Add DRG + Private Load Balancer |
| No load balancer explicitly listed | Add one (public or private based on other signals) |
| No bastion explicitly listed | Add one in Public Subnet |

### What to never ask about
- Whether to include gateways (always add the appropriate ones)
- Whether to include WAF (always add when internet-facing)
- Subnet count or naming (derive from tier model, see below)
- Icon style or colour (always use OCI standards)
- Page size or direction (always A3 landscape, always TB)

---

## Canonical OCI Diagram Layout

All generated diagrams follow a **top-to-bottom (TB)** layout on an
**A3 landscape** canvas (1654 × 1169 px). This matches Oracle's published
reference architectures.

### Box hierarchy (outer → inner)

```
Oracle Cloud Infrastructure (Region)        outermost box
  ├── Regional subnets  ─────────────────── drawn ABOVE AD boxes
  │     Private Subnet — Private Load Balancer + DRG
  │     Public Subnet  — Bastion Host
  │     Public Subnet  — WAF + Public Load Balancer (SSL offload)
  │
  └── Availability Domain N  ────────────── one or two, side by side
        ├── Fault Domain 1 │ Fault Domain 2  only in single-AD active-active
        │     Private Subnet  Web Tier
        │     App Tier compute nodes
        │
        ├── Private Subnet  Web Tier  ────── in multi-AD (no FD boxes)
        ├── Private Subnet  App Tier
        └── Private Subnet  DB Tier  ─────── always bottom; spans both ADs
                                             or both FDs
```

### Three topology templates

#### Template A — Single AD (default / active-active with FDs)
```
Region box
  Regional subnets (LB, Bastion, WAF+LB)
  AD1 (large)
    FD1 | FD2  (side by side)
      Private Subnet  Web Tier  (one per FD)
      App Tier nodes            (one set per FD)
    Shared File System          (between FDs)
    Private Subnet  DB Tier     (bottom, spans both FDs)
  AD2 placeholder               (small box, right side)
```

#### Template B — Multi-AD (active-passive)
```
Region box
  Regional subnets (LB, Bastion, WAF+LB)  ← outside AD boxes
  AD1 (primary)           | AD2 (standby)  ← side by side
    Private Subnet Web Tier  Private Subnet Web Tier
    Private Subnet App Tier  Private Subnet App Tier
    Shared File System       Shared File System
  Private Subnet DB Tier  (spans both ADs, DataGuard sync)
```

#### Template C — Multi-Region (active-passive DR)
```
Region box: Primary          Region box: Standby
  Regional subnets             Regional subnets
  AD1                          AD1
    Private Subnet Web Tier      Private Subnet Web Tier
    Private Subnet App Tier      Private Subnet App Tier
    Shared File System           Shared File System
    Private Subnet DB Tier       Private Subnet DB Tier
  NAT GW / Service GW          NAT GW / Service GW
                    ↕ VCN Peering / DataGuard ASYNC / rsync
```

---

## External Elements

Elements outside the Region box follow fixed placement conventions.

### Left side (on-premises connectivity)
| Element | Notes |
|---------|-------|
| CPE | Customer Premises Equipment router |
| VPN / IPSec | Connects CPE to DRG |
| FastConnect | Connects CPE to DRG (alternative / additional) |
| DRG | Straddling left edge of Region/VCN box |
| DNS | On-premises DNS server |
| Internal Users | End-user icon |
| epminternal.mycompany.com | Internal FQDN label |

### Right side (OCI managed services)
| Element | Notes |
|---------|-------|
| NAT Gateway | Straddling right edge of Region/VCN box |
| Service Gateway | Below NAT GW, straddling right edge |
| Third Party Integrations | Outbound from App Tier via NAT |
| App/DB Backups | Target of Service GW |
| YUM Repo | Target of Service GW (OS updates) |
| Object Storage | Target of Service GW (backups) |

### Top (internet-facing)
| Element | Notes |
|---------|-------|
| Public Internet | Cloud icon |
| Admins | Admin user icon, SSH path |
| epm.mycompany.com | Public FQDN label |
| Workstation / Browser / FR Studio | End-user workstation |
| Internet Gateway | Straddling top edge of Region box |
| WAF | Between IGW and Public LB |

---

## Gateway Placement Rules

| Gateway | Position | Behaviour |
|---------|----------|-----------|
| Internet Gateway | Top of VCN/Region box | Straddles top edge |
| DRG | Left of VCN/Region box | Straddles left edge |
| NAT Gateway | Right of VCN/Region box | Straddles right edge |
| Service Gateway | Right of VCN/Region box, below NAT GW | Straddles right edge |

---

## Subnet Colour Conventions

| Subnet type | Fill | Border | Style |
|-------------|------|--------|-------|
| Public Subnet | White / light blue | Orange, solid | Rounded |
| Private Subnet | Light grey | Orange, dashed | Rounded |
| AD box | Light grey | Grey, solid | Rounded |
| FD box | None / white | Grey, dashed | Rounded |
| Region box | Light grey | Grey, solid | Rounded |
| DB Tier subnet | Darker grey | Orange, dashed | Rounded |

---

## Modules

### `agent/bom_parser.py`

Parses an Excel BOM using `openpyxl`. Maps SKUs and description keywords to
OCI service types, then injects best-practice services (gateways, WAF,
logging, monitoring, etc.) regardless of BOM content.

Key functions:
- `parse_bom(xlsx_path) → list[ServiceItem]`
- `build_llm_prompt(items, context="") → str`
- `bom_to_llm_input(xlsx_path, context="") → (items, prompt)`

The prompt instructs the LLM to apply the assumption table above and only
ask clarification questions for blocking unknowns.

### `agent/layout_engine.py`

Takes the LLM's layout spec JSON and computes absolute pixel positions for
every node, subnet box, AD box, FD box, and Region box on the A3 canvas.

Layout is **deterministic** — no LLM creativity here. Rules:

| Concern | Rule |
|---------|------|
| Direction | Top → bottom (TB) |
| Tier order (top to bottom) | regional subnets → web → app → db |
| AD / FD arrangement | Side by side (left / right) |
| Icon size | 48 × 48 px, label below |
| IGW | Straddle top of Region box |
| DRG | Straddle left of Region box |
| NAT GW | Straddle right of Region box |
| Service GW | Straddle right of Region box, below NAT |
| Region box | Computed bounding rect of all AD boxes + regional subnets |
| AD box | Computed bounding rect of its subnet / FD boxes |
| FD box | Computed bounding rect of its subnet boxes |
| Compartment box | Wraps Region box + right-side OCI services |

Key functions:
- `compute_positions(layout_spec) → (nodes, groups)`
- `spec_to_draw_dict(layout_spec, items_by_id) → draw_dict`

### `agent/drawio_generator.py`

Converts the positioned draw dict into draw.io XML. All cells are emitted
at `parent="1"` (root) — **flat structure**, nothing nested. Icons sit
visually inside subnet boxes but are not children, so every element is
independently draggable in draw.io.

Draw order (first emitted = furthest back in z-order):
1. Compartment box
2. Region box(es)
3. AD boxes
4. FD boxes
5. Subnet boxes
6. Icon nodes
7. Edges

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

The layout compiler (OCI GenAI Agent) must return one of two JSON shapes.

### Success — layout spec

```json
{
  "deployment_type": "single_ad",
  "page": {"width": 1654, "height": 1169},
  "regions": [
    {
      "id": "region_primary",
      "label": "Oracle Cloud Infrastructure (Region)",
      "availability_domains": [
        {
          "id": "ad1",
          "label": "Availability Domain 1",
          "fault_domains": [
            {
              "id": "fd1",
              "label": "Fault Domain 1",
              "subnets": [
                {
                  "id": "web_sub_fd1",
                  "label": "Private Subnet",
                  "tier": "web",
                  "nodes": [{"id": "web_1", "type": "compute", "label": "Web Tier"}]
                }
              ]
            },
            {
              "id": "fd2",
              "label": "Fault Domain 2",
              "subnets": [
                {
                  "id": "web_sub_fd2",
                  "label": "Private Subnet",
                  "tier": "web",
                  "nodes": [{"id": "web_2", "type": "compute", "label": "Web Tier"}]
                }
              ]
            }
          ],
          "subnets": [
            {
              "id": "db_sub",
              "label": "Private Subnet",
              "tier": "db",
              "nodes": [
                {"id": "db_1", "type": "database", "label": "EPM Database"},
                {"id": "db_2", "type": "database", "label": "Foundation Database"}
              ]
            }
          ]
        }
      ],
      "regional_subnets": [
        {
          "id": "pub_sub_bastion",
          "label": "Public Subnet",
          "tier": "ingress",
          "nodes": [{"id": "bastion_1", "type": "compute", "label": "Bastion Host"}]
        },
        {
          "id": "pub_sub_lb",
          "label": "Public Subnet",
          "tier": "ingress",
          "nodes": [
            {"id": "waf_1",    "type": "waf",           "label": "WAF"},
            {"id": "pub_lb_1", "type": "load balancer", "label": "Load Balancer"}
          ]
        },
        {
          "id": "priv_sub_lb",
          "label": "Private Subnet",
          "tier": "ingress",
          "nodes": [{"id": "priv_lb_1", "type": "load balancer", "label": "Load Balancer"}]
        }
      ],
      "gateways": [
        {"id": "igw_1",  "type": "internet gateway", "label": "Internet Gateway", "position": "top"},
        {"id": "drg_1",  "type": "drg",              "label": "DRG",              "position": "left"},
        {"id": "nat_1",  "type": "nat gateway",      "label": "NAT Gateway",      "position": "right"},
        {"id": "sgw_1",  "type": "service gateway",  "label": "Service Gateway",  "position": "right"}
      ],
      "oci_services": [
        {"id": "obj_storage", "type": "object storage", "label": "Object Storage"},
        {"id": "yum_repo",    "type": "compute",         "label": "YUM Repo"}
      ]
    }
  ],
  "external": [
    {"id": "on_prem",  "type": "on premises",  "label": "On-Premises"},
    {"id": "internet", "type": "internet",      "label": "Public Internet"},
    {"id": "admins",   "type": "users",         "label": "Admins"}
  ],
  "edges": [
    {"id": "e1", "source": "on_prem",  "target": "drg_1",   "label": "FastConnect"},
    {"id": "e2", "source": "internet", "target": "igw_1",   "label": "HTTPS/443"},
    {"id": "e3", "source": "igw_1",    "target": "waf_1",   "label": "HTTPS/443"},
    {"id": "e4", "source": "waf_1",    "target": "pub_lb_1","label": "HTTPS/443"},
    {"id": "e5", "source": "nat_1",    "target": "internet", "label": "Outbound"}
  ]
}
```

#### `deployment_type` values

| Value | Template |
|-------|----------|
| `single_ad` | Template A — one AD, Fault Domains inside |
| `multi_ad` | Template B — two ADs side by side, no FDs |
| `multi_region` | Template C — two Region boxes side by side |

### Clarification needed

Only returned when the answer would materially change the topology and
**cannot** be safely defaulted (e.g. customer explicitly mentions DR across
regions but does not say how many).

```json
{
  "status": "need_clarification",
  "questions": ["How many regions should the DR topology span?"]
}
```

**Do not ask about:** gateway inclusion, WAF, subnet naming, icon styles,
page size, or anything covered by the default assumption table.

---

## Fixed Edges (always injected by layout engine)

These are added regardless of what the LLM returns.

| Source | Target | Label |
|--------|--------|-------|
| Internet Gateway | Public LB subnet | HTTPS/443 |
| DRG | Private LB subnet | HTTP |
| Public LB | Web Tier subnet | HTTP |
| Private LB | Web Tier subnet | HTTP |
| Web Tier subnet | App Tier subnet | HTTP |
| App Tier subnet | DB Tier subnet | Data Access |
| NAT Gateway | Internet (external) | Outbound |
| Service Gateway | OCI Services | Internal |

DataGuard and rsync edges are injected for multi-AD and multi-region
topologies automatically based on `deployment_type`.

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

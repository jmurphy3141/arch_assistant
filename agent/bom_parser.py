"""
agent/bom_parser.py
--------------------
Reads BOM Excel → produces a clean service list for the LLM layout compiler.
The LLM receives a structured summary and returns a hierarchical layout spec JSON.
This file does NOT decide layout — only service identification.

The LLM prompt uses an assumption-first approach: apply defaults from the
default assumption table, never ask about things that can be inferred.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── SKU → (oci_type, default_layer) ─────────────────────────────────────────
SKU_MAP: dict[str, tuple] = {
    "B94176": ("compute",           "compute"),
    "B94177": (None,                None),       # memory — part of compute
    "B91961": (None,                None),       # block storage — implied
    "B91962": (None,                None),       # block perf — implied
    "B99060": ("database",          "data"),
    "B99062": (None,                None),       # db storage — implied
    "B91628": ("object storage",    "data"),
    "B93030": ("load balancer",     "ingress"),
    "B93031": (None,                None),       # LB bandwidth — implied
    "B88325": ("drg",               "ingress"),
    "B90618": ("functions",         "compute"),
    "B90617": (None,                None),       # fn execution — part of functions
    "B92072": ("api gateway",       "ingress"),
    "B95697": ("queue",             "async"),
}

DESC_MAP: dict[str, tuple] = {
    "container instances": ("container engine", "compute"),
    "bastion":             ("bastion",           "ingress"),
    "identity and access": ("iam",               "data"),
    "network load balancer":("load balancer",    "ingress"),
    "secrets on oci vault": ("vault",            "data"),
}

# Best-practice additions (always injected)
BEST_PRACTICE = [
    {"id": "internet_gateway", "type": "internet gateway", "label": "Internet Gateway",     "layer": "external"},
    {"id": "nat_gateway",      "type": "nat gateway",      "label": "NAT Gateway",           "layer": "ingress"},
    {"id": "service_gateway",  "type": "service gateway",  "label": "Service Gateway",       "layer": "ingress"},
    {"id": "waf",              "type": "waf",              "label": "WAF",                   "layer": "ingress"},
    {"id": "network_firewall", "type": "waf",              "label": "Network Firewall",      "layer": "ingress"},
    {"id": "logging",          "type": "logging",          "label": "Logging Analytics\n(SIEM)", "layer": "data"},
    {"id": "monitoring",       "type": "monitoring",       "label": "Monitoring + APM",      "layer": "data"},
    {"id": "db_mgmt",          "type": "monitoring",       "label": "DB Management",         "layer": "data"},
    {"id": "directory",        "type": "iam",              "label": "Directory Services",    "layer": "data"},
    {"id": "certificates",     "type": "vault",            "label": "Certificates",          "layer": "data"},
]


@dataclass
class ServiceItem:
    id:       str
    oci_type: str
    label:    str
    layer:    str           # external | ingress | compute | async | data
    quantity: Optional[float] = None
    notes:    str = ""


def parse_bom(xlsx_path: str | Path, context: str = "") -> list[ServiceItem]:
    """Parse BOM Excel → list of ServiceItems ready for LLM layout prompt."""
    import openpyxl
    wb   = openpyxl.load_workbook(xlsx_path, data_only=True)
    bom  = wb["BOM"]
    inp  = wb["Input"] if "Input" in wb.sheetnames else None

    # Read input sheet for quantities
    input_data: dict[str, dict] = {}
    if inp:
        hdrs = None
        for row in inp.iter_rows(values_only=True):
            if hdrs is None:
                hdrs = [str(c).lower() if c else "" for c in row]
                continue
            if row[0]:
                input_data[str(row[0]).lower()] = {
                    hdrs[i]: row[i] for i in range(1, len(row)) if i < len(hdrs)
                }

    app_ocpu = int((input_data.get("ec2", {}).get("vcpu count", 0) or 0) / 2)
    db_ocpu  = int((input_data.get("postgres rds", {}).get("vcpu count", 0) or 0) / 2)
    obj_gb   = input_data.get("postgres rds", {}).get("storage (gb)", 0) or 0

    items: list[ServiceItem] = []
    seen_types: set[str] = set()
    counters: dict[str, int] = {}

    hdrs = None
    for row in bom.iter_rows(values_only=True):
        if hdrs is None:
            hdrs = [str(c).lower().strip() if c else f"c{i}" for i, c in enumerate(row)]
            continue
        if not any(v is not None for v in row):
            continue
        d = dict(zip(hdrs, row))

        sku  = str(d.get("sku", "") or "").strip()
        desc = str(d.get("description", "") or "").lower().strip()
        qty  = d.get("quantity")
        note = str(d.get(hdrs[-1], "") or "")

        # SKU lookup
        if sku in SKU_MAP:
            oci_type, layer = SKU_MAP[sku]
            if not oci_type or not layer:
                continue
        else:
            # Description lookup
            oci_type, layer = None, None
            for key, (t, l) in DESC_MAP.items():
                if key in desc:
                    oci_type, layer = t, l
                    break
            if not oci_type:
                continue

        # Deduplicate by oci_type (one icon per service type)
        if oci_type in seen_types:
            continue
        seen_types.add(oci_type)

        counters[oci_type] = counters.get(oci_type, 0) + 1
        nid = f"{oci_type.replace(' ', '_')}_{counters[oci_type]}"

        # Build label with quantity context
        label = _make_label(oci_type, qty, app_ocpu, db_ocpu, obj_gb, note)

        items.append(ServiceItem(id=nid, oci_type=oci_type, label=label,
                                 layer=layer, quantity=qty, notes=note))

    # Add On-Premises (always — FastConnect implies it)
    items.insert(0, ServiceItem(id="on_prem", oci_type="on premises",
                                label="On-Premises\n(3 Offices)", layer="external"))

    # Add best-practice services
    for bp in BEST_PRACTICE:
        if bp["type"] not in seen_types:
            items.append(ServiceItem(id=bp["id"], oci_type=bp["type"],
                                     label=bp["label"], layer=bp["layer"],
                                     notes="best practice"))
            seen_types.add(bp["type"])

    # ── Baseline injection: Internet ─────────────────────────────────────────
    # Inject when IGW is present (always true after BEST_PRACTICE), unless suppressed.
    if (
        "internet gateway" in seen_types
        and "NO_INTERNET_ENDPOINT=true" not in context
        and "internet" not in seen_types
        and not any(i.id == "internet" for i in items)
    ):
        items.append(ServiceItem(id="internet", oci_type="internet",
                                 label="Public Internet", layer="external",
                                 notes="injected_baseline"))
        seen_types.add("internet")

    # ── Baseline injection: Bastion ──────────────────────────────────────────
    # Inject when any compute or database workload exists, unless suppressed.
    has_workload = any(i.oci_type in {"compute", "database"} for i in items)
    if (
        has_workload
        and "NO_BASTION=true" not in context
        and "bastion" not in seen_types
        and not any(i.id == "bastion_1" for i in items)
    ):
        items.append(ServiceItem(id="bastion_1", oci_type="bastion",
                                 label="Bastion", layer="ingress",
                                 notes="injected_baseline"))
        seen_types.add("bastion")

    return items


def _make_label(oci_type: str, qty, app_ocpu: int, db_ocpu: int, obj_gb: float, note: str) -> str:
    labels = {
        "compute":      f"Compute\n×{app_ocpu:,} OCPU",
        "database":     f"PostgreSQL DB\n×{db_ocpu:,} OCPU",
        "object storage": f"Object Storage\n{int(obj_gb*2/1024)} TB",
        "load balancer": f"Load Balancer\n×{int(qty) if qty else '?'} (per region)",
        "drg":          f"DRG / FastConnect\n×{int(qty) if qty else '?'} ports",
        "functions":    "OCI Functions\n~10k calls/day",
        "api gateway":  "API Gateway",
        "queue":        f"Queue\n{int(qty) if qty else '?'}M req/month",
        "container engine": "Container Instances",
        "bastion":      "Bastion",
        "iam":          "IAM",
        "load balancer_2": f"NLB ×3",
        "vault":        "Vault (Secrets)",
    }
    return labels.get(oci_type, oci_type.title())


def build_llm_prompt(items: list[ServiceItem], context: str = "") -> str:
    """Build the layout compiler prompt for the OCI GenAI agent.

    Uses an assumption-first approach: the LLM must apply the default assumption
    table and NEVER ask about things it can infer. Clarification is only for
    truly blocking topology decisions.

    context: optional text from a requirements/notes file uploaded alongside the BOM.

    Returns a prompt that instructs the LLM to output the new hierarchical spec:
    regions → availability_domains → fault_domains → subnets → nodes.
    """
    service_list = "\n".join(
        f'  {{"id": "{i.id}", "type": "{i.oci_type}", "label": "{i.label.replace(chr(10), " ")}", "suggested_layer": "{i.layer}"}}'
        for i in items
    )

    context_block = (
        f"\nADDITIONAL CONTEXT:\n{context.strip()}\n"
        if context and context.strip() else ""
    )

    return f"""You are an OCI architecture layout compiler. Your job is to produce a deterministic
hierarchical layout specification JSON for an OCI draw.io diagram.
{context_block}
═══════════════════════════════════════════════════════
ASSUMPTION-FIRST RULE (CRITICAL)
═══════════════════════════════════════════════════════
Apply the default assumption table below for any missing information.
NEVER ask about things you can infer. Only ask clarification questions for
information that would materially change the topology AND cannot be safely
assumed from context.

DEFAULT ASSUMPTION TABLE:
┌─────────────────────────────────────────────┬──────────────────────────────────────────────┐
│ Signal in BOM / context                     │ Assumed topology                             │
├─────────────────────────────────────────────┼──────────────────────────────────────────────┤
│ No HA signal at all                         │ single_ad, single FD                         │
│ "HA" or redundancy mentioned                │ single_ad, two Fault Domains                 │
│ Two ADs mentioned, or "regional HA"         │ multi_ad, two ADs side by side               │
│ "DR", "multi-region", or two regions        │ multi_region, active-passive                 │
│ "active-active" explicit                    │ single_ad, two Fault Domains (active-active) │
│ Database in BOM                             │ Add Data Guard (sync multi_ad, async multi_region) │
│ Any compute in BOM                          │ Add NAT Gateway                              │
│ Any OCI managed service                     │ Add Service Gateway                          │
│ External users / HTTPS in BOM or context    │ Add IGW + WAF + Public Load Balancer         │
│ On-prem / FastConnect in BOM                │ Add DRG + Private Load Balancer              │
│ No load balancer listed                     │ Add one (public or private per other signals)│
└─────────────────────────────────────────────┴──────────────────────────────────────────────┘

NEVER ASK ABOUT:
- Whether to include gateways (always add the appropriate ones based on signals above)
- Whether to include WAF (always add when internet-facing)
- Subnet count or naming (derive from tier model below)
- Icon styles or colours (always use OCI standards)
- Page size or layout direction (always A3 landscape 1654×1169, always TB)

═══════════════════════════════════════════════════════
INPUT SERVICES (from BOM):
═══════════════════════════════════════════════════════
[
{service_list}
]

═══════════════════════════════════════════════════════
LAYOUT RULES
═══════════════════════════════════════════════════════
Layout direction: TOP → BOTTOM (TB)
Canvas: 1654 × 1169 px (A3 landscape)

SUBNET TIER MODEL (top to bottom inside each AD):
  ingress — Public Subnet: WAF, Public Load Balancer, Bastion
  ingress — Private Subnet: Private Load Balancer, DRG connectivity
  web     — Private Subnet: Web Tier compute
  app     — Private Subnet: App Tier compute, Functions, API Gateway, Queues
  db      — Private Subnet: Databases, Vault

PLACEMENT RULES:
1. Regional subnets (LB, Bastion, WAF) are placed ABOVE the AD boxes, inside the region.
2. Gateways straddle the region box edges:
   - internet_gateway → top edge (position: "top")
   - drg              → left edge (position: "left")
   - nat_gateway      → right edge (position: "right")
   - service_gateway  → right edge, below NAT (position: "right")
3. OCI managed services (Object Storage, IAM, Logging, Monitoring) → oci_services list.
4. External elements (on-premises, internet users, CPE) → external list.
   - id="internet" (type "internet") MUST appear in external[].
5. For single_ad: include fault_domains[] inside the AD; subnets at AD level = shared tiers (DB).
6. For multi_ad / multi_region: no fault_domains — subnets directly inside each AD.
7. id="bastion_1" (type "bastion") MUST be placed inside a regional_subnets[] ingress subnet.

═══════════════════════════════════════════════════════
CLARIFICATION (ONLY IF TRULY BLOCKING)
═══════════════════════════════════════════════════════
If — and ONLY if — there is a specific topology decision that cannot be determined
from the BOM, context, or default assumption table, return ONLY:
{{
  "status": "need_clarification",
  "questions": ["<single concise question>"]
}}
Keep it to at most one or two truly blocking questions. Do NOT ask about gateways,
WAF, subnet naming, icon styles, page size, or anything in the assumption table.

═══════════════════════════════════════════════════════
OUTPUT JSON SCHEMA (use this exact structure)
═══════════════════════════════════════════════════════
{{
  "deployment_type": "single_ad",
  "page": {{"width": 1654, "height": 1169}},
  "regions": [
    {{
      "id": "region_primary",
      "label": "Oracle Cloud Infrastructure (Region)",
      "regional_subnets": [
        {{
          "id": "pub_sub_lb",
          "label": "Public Subnet",
          "tier": "ingress",
          "nodes": [
            {{"id": "waf_1",    "type": "waf",           "label": "WAF"}},
            {{"id": "pub_lb_1", "type": "load balancer", "label": "Load Balancer"}}
          ]
        }},
        {{
          "id": "pub_sub_bastion",
          "label": "Public Subnet",
          "tier": "ingress",
          "nodes": [{{"id": "bastion_1", "type": "bastion", "label": "Bastion Host"}}]
        }}
      ],
      "availability_domains": [
        {{
          "id": "ad1",
          "label": "Availability Domain 1",
          "fault_domains": [
            {{
              "id": "fd1",
              "label": "Fault Domain 1",
              "subnets": [
                {{
                  "id": "web_sub_fd1",
                  "label": "Private Subnet",
                  "tier": "web",
                  "nodes": [{{"id": "web_1", "type": "compute", "label": "Web Tier"}}]
                }},
                {{
                  "id": "app_sub_fd1",
                  "label": "Private Subnet",
                  "tier": "app",
                  "nodes": [{{"id": "app_1", "type": "compute", "label": "App Tier"}}]
                }}
              ]
            }}
          ],
          "subnets": [
            {{
              "id": "db_sub",
              "label": "Private Subnet",
              "tier": "db",
              "nodes": [{{"id": "db_1", "type": "database", "label": "PostgreSQL DB"}}]
            }}
          ]
        }}
      ],
      "gateways": [
        {{"id": "igw_1",  "type": "internet gateway", "label": "Internet Gateway", "position": "top"}},
        {{"id": "drg_1",  "type": "drg",              "label": "DRG",              "position": "left"}},
        {{"id": "nat_1",  "type": "nat gateway",      "label": "NAT Gateway",      "position": "right"}},
        {{"id": "sgw_1",  "type": "service gateway",  "label": "Service Gateway",  "position": "right"}}
      ],
      "oci_services": [
        {{"id": "obj_storage_1", "type": "object storage", "label": "Object Storage"}},
        {{"id": "logging_1",     "type": "logging",         "label": "Logging Analytics"}}
      ]
    }}
  ],
  "external": [
    {{"id": "on_prem",  "type": "on premises",  "label": "On-Premises"}},
    {{"id": "internet", "type": "internet",      "label": "Public Internet"}},
    {{"id": "admins",   "type": "users",         "label": "Admins"}}
  ],
  "edges": [
    {{"id": "e1", "source": "on_prem",   "target": "drg_1",    "label": "FastConnect"}},
    {{"id": "e2", "source": "internet",  "target": "igw_1",    "label": "HTTPS/443"}},
    {{"id": "e3", "source": "igw_1",     "target": "waf_1",    "label": "HTTPS/443"}},
    {{"id": "e4", "source": "waf_1",     "target": "pub_lb_1", "label": "HTTPS/443"}},
    {{"id": "e5", "source": "nat_1",     "target": "internet", "label": "Outbound"}}
  ]
}}

IMPORTANT RULES FOR OUTPUT:
1. Use ONLY services from the INPUT SERVICES list above. Do not invent new services.
2. Every service from the INPUT list must appear exactly once in the output.
3. Assign IDs exactly as given in the INPUT (e.g. "compute_1", "database_1").
4. deployment_type must be one of: "single_ad", "multi_ad", "multi_region".
5. For multi_ad: two availability_domains side by side, no fault_domains.
6. For multi_region: two entries in regions[], each a full single-AD layout.
7. Apply the default assumption table — do NOT ask if you can infer.
8. Output ONLY valid JSON. No markdown, no prose, no code fences."""


def bom_to_llm_input(xlsx_path: str | Path, context: str = "") -> tuple[list[ServiceItem], str]:
    """Main entry point: parse BOM and return (items, llm_prompt).

    context: optional free-text from an uploaded requirements/notes file.
    """
    items = parse_bom(xlsx_path, context=context)
    prompt = build_llm_prompt(items, context=context)
    return items, prompt

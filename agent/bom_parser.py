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
    # Compute — VM shapes (all generations map to "compute")
    "B94176":  ("compute",           "compute"),   # E3/E4 OCPU
    "B94177":  (None,                None),        # E3/E4 memory — part of compute
    "B111129": ("compute",           "compute"),   # E6 OCPU
    "B111130": (None,                None),        # E6 memory — part of compute
    "B88317":  ("compute",           "compute"),   # A1 Flex OCPU
    "B88318":  (None,                None),        # A1 memory — part of compute
    # Block Storage
    "B91961":  (None,                None),        # block volume storage — implied by compute
    "B91962":  (None,                None),        # block volume perf — implied
    # Database
    "B99060":  ("database",          "data"),
    "B99062":  (None,                None),        # db storage — implied
    # Object Storage
    "B91628":  ("object storage",    "data"),
    # Load Balancer
    "B93030":  ("load balancer",     "ingress"),
    "B93031":  (None,                None),        # LB bandwidth — implied
    # FastConnect / DRG
    "B88325":  ("drg",               "ingress"),   # FastConnect 1 Gbps
    "B88326":  ("drg",               "ingress"),   # FastConnect 10 Gbps
    "B88327":  ("drg",               "ingress"),   # FastConnect 100 Gbps
    # Functions
    "B90618":  ("functions",         "compute"),
    "B90617":  (None,                None),        # fn execution — part of functions
    # API Gateway / Queue
    "B92072":  ("api gateway",       "ingress"),
    "B95697":  ("queue",             "async"),
}

DESC_MAP: dict[str, tuple] = {
    # Compute shapes — keyword order matters (most-specific first)
    "bare metal":             ("bare metal",       "compute"),
    "bm.optimized":           ("bare metal",       "compute"),  # HPC BM.Optimized3.36
    "bm.hpc":                 ("bare metal",       "compute"),
    "rdma":                   ("bare metal",       "compute"),
    "container instances":    ("container engine", "compute"),
    "oke enhanced":           ("container engine", "compute"),
    "oke":                    ("container engine", "compute"),
    "kubernetes":             ("container engine", "compute"),
    "gpu":                    ("compute",          "compute"),  # GPU shapes
    "vm.standard":            ("compute",          "compute"),  # any VM.Standard shape
    "vm.optimized":           ("compute",          "compute"),
    "flex":                   ("compute",          "compute"),  # E3/E4/E6.Flex
    "standard - e":           ("compute",          "compute"),  # E3/E4/E6 OCPU/memory rows
    "standard - a":           ("compute",          "compute"),  # Ampere A1
    "ocpu per hour":          ("compute",          "compute"),  # any OCPU billing row
    # Database
    "autonomous database":    ("database",         "data"),
    "mysql":                  ("database",         "data"),
    "postgresql":             ("database",         "data"),
    "nosql":                  ("database",         "data"),
    "exadata":                ("database",         "data"),
    "base database":          ("database",         "data"),
    # Storage
    "file storage":           ("file storage",     "data"),
    "object storage":         ("object storage",   "data"),
    "block volume":           (None,               None),       # implied by compute — skip
    # Networking / ingress
    "fastconnect":            ("drg",              "ingress"),
    "network load balancer":  ("load balancer",    "ingress"),
    "load balancer":          ("load balancer",    "ingress"),
    "api gateway":            ("api gateway",      "ingress"),
    "bastion":                ("bastion",          "ingress"),
    "waf":                    ("waf",              "ingress"),
    # Async / integration
    "streaming":              ("queue",            "async"),
    "queue":                  ("queue",            "async"),
    "kafka":                  ("queue",            "async"),
    "functions":              ("functions",        "compute"),
    # Management / security
    "identity and access":    ("iam",              "data"),
    "vault":                  ("vault",            "data"),
    "secrets on oci vault":   ("vault",            "data"),
    "logging":                ("logging",          "data"),
    "monitoring":             ("monitoring",       "data"),
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
    bom  = wb["BOM"] if "BOM" in wb.sheetnames else wb.worksheets[0]
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
    q = int(qty) if qty else "?"
    labels = {
        "compute":        f"Compute VM\n×{q}" if app_ocpu == 0 else f"Compute\n×{app_ocpu:,} OCPU",
        "bare metal":     f"HPC BM.Optimized3.36\n×{q} nodes",
        "database":       f"PostgreSQL DB\n×{db_ocpu:,} OCPU",
        "object storage": f"Object Storage\n{int(obj_gb*2/1024)} TB",
        "load balancer":  f"Load Balancer\n×{q} (per region)",
        "drg":            f"DRG / FastConnect\n×{q} ports",
        "functions":      "OCI Functions\n~10k calls/day",
        "api gateway":    "API Gateway",
        "queue":          f"Queue\n{q}M req/month",
        "container engine": "OKE Enhanced Cluster",
        "file storage":   "File Storage\nNFS PVC",
        "bastion":        "Bastion",
        "iam":            "IAM",
        "load balancer_2": "NLB ×3",
        "vault":          "Vault (Secrets)",
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


# ── Topology pattern detection ────────────────────────────────────────────────

_TOPOLOGY_HPC_OKE       = "HPC_OKE"
_TOPOLOGY_DATA_PLATFORM = "DATA_PLATFORM"
_TOPOLOGY_3TIER         = "STANDARD_3TIER"

# Pre-declared groups per topology (injected as directives, not suggestions)
_TOPOLOGY_GROUPS: dict[str, list[dict]] = {
    _TOPOLOGY_HPC_OKE: [
        {"id": "bas_sub_box",    "label": "Bastion Subnet (Public)", "order": 0},
        {"id": "cp_sub_box",     "label": "Control Plane Subnet",    "order": 1},
        {"id": "worker_sub_box", "label": "Worker Subnet (Private)", "order": 2},
        {"id": "storage_sub_box","label": "Storage Subnet",          "order": 3},
    ],
    _TOPOLOGY_DATA_PLATFORM: [
        {"id": "ingest_sub_box", "label": "Ingest Subnet",    "order": 0},
        {"id": "proc_sub_box",   "label": "Processing Subnet","order": 1},
        {"id": "store_sub_box",  "label": "Storage Subnet",   "order": 2},
    ],
    _TOPOLOGY_3TIER: [
        {"id": "pub_sub_box",    "label": "Public Subnet",    "order": 0},
        {"id": "app_sub_box",    "label": "App Subnet",       "order": 1},
        {"id": "db_sub_box",     "label": "DB Subnet",        "order": 2},
    ],
}

# Explicit group assignment per oci_type per topology
_TOPOLOGY_GROUP_MAP: dict[str, dict[str, str]] = {
    _TOPOLOGY_HPC_OKE: {
        "bastion":          "bas_sub_box",
        "waf":              "bas_sub_box",
        "load balancer":    "bas_sub_box",
        "container engine": "cp_sub_box",
        "bare metal":       "worker_sub_box",
        "compute":          "worker_sub_box",
        "file storage":     "storage_sub_box",
        "database":         "storage_sub_box",
        "vault":            "storage_sub_box",
    },
    _TOPOLOGY_DATA_PLATFORM: {
        "waf":              "ingest_sub_box",
        "load balancer":    "ingest_sub_box",
        "bastion":          "ingest_sub_box",
        "api gateway":      "ingest_sub_box",
        "queue":            "ingest_sub_box",
        "functions":        "proc_sub_box",
        "compute":          "proc_sub_box",
        "container engine": "proc_sub_box",
        "database":         "store_sub_box",
        "file storage":     "store_sub_box",
        "vault":            "store_sub_box",
    },
    _TOPOLOGY_3TIER: {
        "waf":              "pub_sub_box",
        "load balancer":    "pub_sub_box",
        "bastion":          "pub_sub_box",
        "compute":          "app_sub_box",
        "container engine": "app_sub_box",
        "functions":        "app_sub_box",
        "api gateway":      "app_sub_box",
        "queue":            "app_sub_box",
        "database":         "db_sub_box",
        "vault":            "db_sub_box",
        "file storage":     "db_sub_box",
    },
}

# oci_types that are never placed inside a subnet group (gateways + managed services)
_NO_GROUP_TYPES = frozenset([
    "internet gateway", "nat gateway", "service gateway", "drg",
    "object storage", "logging", "monitoring", "iam", "certificates",
    "on premises", "internet", "users", "admins", "workstation",
])


def _detect_topology_pattern(items: list[ServiceItem]) -> str:
    """Classify the architecture pattern from service types (deterministic, no LLM)."""
    types = {i.oci_type for i in items}
    has_bare_metal = "bare metal" in types
    has_oke        = "container engine" in types
    has_fss        = "file storage" in types

    # Bare metal alone or OKE + FSS both signal HPC workloads
    if has_bare_metal or (has_oke and has_fss):
        return _TOPOLOGY_HPC_OKE

    # Streaming-dominated, no bare metal, no significant relational DB
    has_streaming = "queue" in types or "streaming" in types
    has_db        = "database" in types
    if has_streaming and not has_bare_metal and not has_db:
        return _TOPOLOGY_DATA_PLATFORM

    return _TOPOLOGY_3TIER


def _build_edge_examples(items: list[ServiceItem], topology: str) -> str:
    """Generate topology-appropriate edge examples using actual service IDs from the BOM."""
    import json as _json
    ids = {i.oci_type: i.id for i in items}  # last id wins (fine for examples)

    edges: list[dict] = []
    ctr = [0]

    def _e(src_type: str, tgt_type: str, label: str) -> None:
        src = ids.get(src_type)
        tgt = ids.get(tgt_type)
        if src and tgt and src != tgt:
            ctr[0] += 1
            edges.append({"id": f"e{ctr[0]}", "source": src, "target": tgt, "label": label})

    if topology == _TOPOLOGY_HPC_OKE:
        # Cross-boundary entry (non-obvious: requires explicit FastConnect link)
        _e("on premises",      "drg",           "FastConnect")
        # Special RDMA fabric between compute nodes (non-obvious high-speed network)
        _e("container engine", "bare metal",    "RDMA")
        # Storage mount (non-obvious: NFS over dedicated storage network)
        _e("bare metal",       "file storage",  "NFS")

    elif topology == _TOPOLOGY_DATA_PLATFORM:
        # Application data-plane paths (non-obvious service-to-service calls)
        _e("api gateway",      "functions",      "Invoke")
        _e("functions",        "queue",          "Enqueue")
        _e("functions",        "database",       "SQL")
        _e("functions",        "object storage", "PUT/GET")

    else:  # STANDARD_3TIER
        # Cross-boundary entry
        _e("on premises",   "drg",          "FastConnect")
        # Application data path (non-obvious tier-to-tier calls)
        _e("load balancer", "compute",      "HTTP")
        _e("compute",       "database",     "SQL/5432")

    return _json.dumps(edges, indent=4)


def build_layout_intent_prompt(
    items: list[ServiceItem],
    questionnaire_text: str = "",
    notes_text: str = "",
    context: str = "",
) -> str:
    """
    Build a compact LayoutIntent prompt.

    Python detects the likely topology from BOM service types and injects it
    as a suggestion.  The LLM owns topology detection and can override based on
    free-text context.  The LLM also declares data-flow edges between services —
    that is the primary value it adds over deterministic Python logic.

    questionnaire_text: answers to a pre-flight questionnaire, if any.
    notes_text:         meeting notes or other free-text input, if any.
    context:            generic context string (e.g. from uploaded context file).
    """
    import json as _json

    topology   = _detect_topology_pattern(items)
    groups     = _TOPOLOGY_GROUPS[topology]
    group_map  = _TOPOLOGY_GROUP_MAP[topology]

    topology_label = {
        _TOPOLOGY_HPC_OKE:       "HPC on OKE (bare metal RDMA + container engine + file storage)",
        _TOPOLOGY_DATA_PLATFORM: "Data Platform (streaming + functions + data lake)",
        _TOPOLOGY_3TIER:         "Standard 3-Tier (web app + compute + database)",
    }[topology]

    # Service list with group hints (suggestions, not mandates)
    def _group_hint(item: ServiceItem) -> str:
        if item.oci_type in _NO_GROUP_TYPES:
            return "null"
        hint = group_map.get(item.oci_type)
        return f'"{hint}"' if hint else "null"

    service_rows = "\n".join(
        f'  {{"id": "{i.id}", "oci_type": "{i.oci_type}", "suggested_layer": "{i.layer}", "group_hint": {_group_hint(i)}}}'
        for i in items
    )

    suggested_groups_json = _json.dumps(groups, indent=4)
    example_groups_json   = _json.dumps(
        [{"id": g["id"], "label": g["label"], "order": g["order"]} for g in groups],
        indent=4,
    )
    edge_examples = _build_edge_examples(items, topology)

    extra_blocks = ""
    if questionnaire_text and questionnaire_text.strip():
        extra_blocks += f"\nQUESTIONNAIRE ANSWERS:\n{questionnaire_text.strip()}\n"
    if notes_text and notes_text.strip():
        extra_blocks += f"\nMEETING / NOTES:\n{notes_text.strip()}\n"
    if context and context.strip():
        extra_blocks += f"\nADDITIONAL CONTEXT:\n{context.strip()}\n"

    return f"""You are an OCI solutions architect and layout compiler.
Given a list of OCI services from a Bill of Materials, you must:
  1. Decide the subnet topology (groups)
  2. Assign each service to the correct layer and group
  3. Declare the data-flow edges between services
  4. Set deployment hints (region count, HA, DR, connectivity)
Output ONLY valid JSON — either a LayoutIntent or a NeedClarification object.
{extra_blocks}
═══════════════════════════════════════════════════════
SUGGESTED TOPOLOGY (detected from BOM service types):
  {topology_label}
═══════════════════════════════════════════════════════
This is a suggestion based on what services are present.
If ADDITIONAL CONTEXT above indicates a different architecture, use that instead.

Suggested groups (use these unless context requires a different topology):
{suggested_groups_json}

Suggested group assignment per service type:
{chr(10).join(f"  {t!r:30s} → {g}" for t, g in group_map.items())}
  — gateways (internet gateway, nat gateway, service gateway, drg) → group=null
  — managed services (object storage, logging, monitoring, iam)    → group=null
  — external (on premises, internet, users)                        → group=null

═══════════════════════════════════════════════════════
INPUT SERVICES (from BOM + baseline injection):
═══════════════════════════════════════════════════════
[
{service_rows}
]

═══════════════════════════════════════════════════════
DEPLOYMENT HINTS (read from context — do not ask)
═══════════════════════════════════════════════════════
region_count:                   count explicit region mentions; default 1
availability_domains_per_region: 2 if "multi-AD" or "HA"; else 1
dr_enabled:                     true only if "DR" or "disaster recovery" stated
on_prem_connectivity:           "fastconnect" if DRG in BOM; "vpn" if VPN; "none" if neither

═══════════════════════════════════════════════════════
STEP 3 — DECLARE DATA-FLOW EDGES (minimal and purposeful)
═══════════════════════════════════════════════════════
OCI diagrams use ELEMENT POSITION to convey most connectivity.
Gateways straddling VCN edges and services grouped in subnets already
tell the reader how traffic flows — no line needed.

ONLY declare an explicit edge when the connection is NON-OBVIOUS from layout:
  ✓ Special network fabrics:    bare_metal ↔ bare_metal  (RDMA / RoCE)
  ✓ Storage mounts:             compute/bare_metal → file_storage  (NFS)
  ✓ Cross-boundary entry point: on_prem → drg  (FastConnect / VPN)
  ✓ Application data path:      compute → database  (SQL)
  ✓ Key API/service calls:      waf → load_balancer, functions → database

NEVER declare these — they are implied by gateway placement and need no line:
  ✗ internet → internet_gateway        (implied: IGW straddles VCN top)
  ✗ internet_gateway → waf/lb/bastion  (implied: IGW on VCN top edge)
  ✗ internet_gateway → region_box      (region_box is a container, not connectable)
  ✗ nat_gateway → internet             (implied: NAT on VCN top/right)
  ✗ service_gateway → any service      (implied: SGW on VCN right edge)
  ✗ drg → bastion / compute            (implied: DRG on VCN left edge)
  ✗ bastion → compute / bare_metal     (implied: co-located in same subnet)

CRITICAL: NEVER use region_box, vcn_box, or any subnet box ID as edge source or target.

Target: 2–5 edges. If the topology has fewer non-obvious connections, output [].

Example edges for this topology (adapt IDs to match INPUT SERVICES exactly):
{edge_examples}

Only declare edges between IDs that exist in INPUT SERVICES.
Use short protocol labels: "HTTPS/443", "SSH", "SQL/5432", "RDMA", "NFS", "Outbound", etc.

CRITICAL: edge source and target must be service icon IDs from INPUT SERVICES only.
NEVER use group/subnet IDs (e.g. bas_sub_box, cp_sub_box, worker_sub_box, storage_sub_box)
as edge source or target — those are layout containers, not connectable services.

═══════════════════════════════════════════════════════
OUTPUT FORMAT — LayoutIntent
═══════════════════════════════════════════════════════
{{
  "schema_version": "1.0",
  "deployment_hints": {{
    "region_count": 1,
    "availability_domains_per_region": 1,
    "dr_enabled": false,
    "on_prem_connectivity": "fastconnect"
  }},
  "groups": {example_groups_json},
  "placements": [
    {{"id": "<exact-id-from-input>", "oci_type": "<type>", "layer": "<layer>", "group": "<group-slug-or-null>"}},
    ...
  ],
  "edges": {edge_examples},
  "assumptions": [
    {{"id": "ha_mode", "statement": "Single AD assumed", "reason": "No HA signal in BOM", "risk": "low"}}
  ],
  "fixed_edges_policy": true
}}

OR — NeedClarification (ONLY if truly blocking):
{{"status": "need_clarification", "questions": [{{"id": "<id>", "question": "...", "blocking": true}}]}}

Allowed question IDs: regions.count, regions.mode, ha.ads, connectivity.onprem, dr.rpo_rto

RULES:
1. Every id from INPUT SERVICES must appear exactly once in placements.
2. Every group slug in placements must appear in the groups array.
3. Use exact id values from INPUT SERVICES — do not rename them.
4. edges must only reference ids that exist in INPUT SERVICES — NEVER group/subnet IDs like bas_sub_box.
5. Output ONLY valid JSON. No markdown, no prose, no code fences."""


def bom_to_llm_input(
    xlsx_path: str | Path,
    context: str = "",
    questionnaire_text: str = "",
    notes_text: str = "",
) -> tuple[list[ServiceItem], str]:
    """Main entry point: parse BOM and return (items, llm_prompt).

    Uses build_layout_intent_prompt() by default (Option 1 architecture).
    context: optional free-text from an uploaded requirements/notes file.
    questionnaire_text: answers to a pre-flight questionnaire, if any.
    notes_text: meeting notes or other free-text, if any.
    """
    items = parse_bom(xlsx_path, context=context)
    prompt = build_layout_intent_prompt(
        items,
        questionnaire_text=questionnaire_text,
        notes_text=notes_text,
        context=context,
    )
    return items, prompt

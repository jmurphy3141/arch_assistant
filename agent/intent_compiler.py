"""
agent/intent_compiler.py
-------------------------
Compile a validated LayoutIntent into the hierarchical spec format that
agent/layout_engine.py's compute_positions() renders natively, producing
region, AD, FD, and subnet boxes in the generated diagram.

Output format:
  {
    "deployment_type": "single_ad" | "multi_ad" | "multi_region",
    "regions": [
      {
        "id":          "region_box",
        "label":       "OCI Region",
        "regional_subnets": [],
        "availability_domains": [
          {
            "id":    "ad1_box",
            "label": "Availability Domain 1",
            "fault_domains": [               # single_ad only — always 3 empty containers
              {"id": "fd1_box", "label": "Fault Domain 1", "subnets": []},
              {"id": "fd2_box", "label": "Fault Domain 2", "subnets": []},
              {"id": "fd3_box", "label": "Fault Domain 3", "subnets": []},
            ],
            "subnets": [                     # grouped subnet boxes with icon nodes
              {
                "id":    "<group_id>",
                "label": "<group_label>",
                "tier":  "ingress" | "app" | "db",
                "nodes": [{"id": ..., "type": ..., "label": ...}, ...],
              },
            ],
          },
        ],
        "gateways":    [{"id": ..., "type": ..., "label": ...}],
        "oci_services": [],
      }
    ],
    "external": [{"id": ..., "type": ..., "label": ...}],
    "edges":    [{"id": ..., "source": ..., "target": ..., "label": ..., ...}],
  }

Gateway nodes (IGW, NAT, DRG, SGW) are placed on region box edges by the
layout engine — they must NOT appear in subnets.

External nodes (on-prem, internet, users, etc.) go in the top-level
"external" list and are positioned outside the region box.

Fixed structural edges injected deterministically:
  on_prem         → region_box  (when on_prem exists)
  internet_gateway → region_box (when IGW exists)

Data-flow edges:
  When the LLM declares intent.edges, those are used verbatim.
  Otherwise a sequential chain between adjacent subnet groups is injected.
"""
from __future__ import annotations

from agent.layout_intent import LayoutIntent, Placement, GROUP_LABELS

# Notes values that are NOT environment names (injected by bom_parser for infra/baseline)
_RESERVED_NOTES = frozenset(["best practice", "injected_baseline", ""])

# OCI gateway types — placed on region box edges by the layout engine
GATEWAY_OCI_TYPES = frozenset([
    "internet gateway", "nat gateway", "service gateway",
    "drg", "dynamic routing gateway",
])

# OCI types that go in the external column/strip (left or top of region)
EXTERNAL_OCI_TYPES = frozenset([
    "on premises", "vpn", "fastconnect", "cpe",
    "internet", "public internet",
    "dns", "users", "user", "admins", "admin",
    "workstation", "browser",
])

# OCI platform services — rendered in the right column inside the region box
# (outside subnet rows, accessible via Service Gateway)
OCI_SERVICE_TYPES = frozenset([
    "object storage", "object store",
    "iam", "identity", "identity and access management",
    "logging", "logging analytics",
    "monitoring", "apm", "application performance monitoring",
    "audit", "auditing",
    "certificates", "certificate service",
    "vault", "key management", "key vault",
    "notifications", "events",
    "service connector", "connector hub",
    "streaming", "queue",
    "api gateway",
    "dns", "traffic management",
    "nosql", "search",
])

# Classic 3-tier fallback when the LLM declares no groups
_GROUP_ORDER = ["pub_sub_box", "app_sub_box", "db_sub_box"]

# Edge label for on_prem → region connectivity
_CONN_LABELS = {
    "fastconnect": "FastConnect",
    "vpn":         "VPN",
    "none":        "",
    "unknown":     "Private Link",
}


class IntentCompileError(ValueError):
    """Raised when the LayoutIntent cannot be compiled into a valid spec."""


def compile_intent_to_flat_spec(
    intent: LayoutIntent,
    items: list,
) -> dict:
    """
    Convert a validated LayoutIntent + ServiceItem list into a hierarchical spec.

    Returns {"deployment_type": ..., "regions": [...], "external": [...], "edges": [...]}.
    """
    items_by_id = {i.id: i for i in (items or [])}
    hints       = intent.deployment_hints

    # ── Classify placements ────────────────────────────────────────────────────
    gateway_placements:  list[Placement] = []
    external_placements: list[Placement] = []
    service_placements:  list[Placement] = []   # OCI platform services → right column
    subnet_placements:   list[Placement] = []

    for p in intent.placements:
        oci_low = p.oci_type.lower()
        if oci_low in GATEWAY_OCI_TYPES:
            gateway_placements.append(p)
        elif p.layer == "external" or oci_low in EXTERNAL_OCI_TYPES:
            external_placements.append(p)
        elif oci_low in OCI_SERVICE_TYPES:
            service_placements.append(p)
        else:
            subnet_placements.append(p)

    # ── Group ordering and labels ──────────────────────────────────────────────
    if intent.groups:
        sorted_decls = sorted(intent.groups, key=lambda g: g.order)
        group_order  = [g.id  for g in sorted_decls]
        group_labels = {g.id: g.label for g in sorted_decls}
    else:
        group_order  = list(_GROUP_ORDER)
        group_labels = dict(GROUP_LABELS)

    # ── Build subnet node lists ────────────────────────────────────────────────
    group_nodes:  dict[str, list[dict]] = {gid: [] for gid in group_order}
    extra_groups: dict[str, list[dict]] = {}  # groups in placements but not declared

    for p in subnet_placements:
        item  = items_by_id.get(p.id)
        label = item.label if item else p.id
        node  = {"id": p.id, "type": p.oci_type, "label": label}
        gid   = p.group or _default_group(p.layer, group_order)
        if gid in group_nodes:
            group_nodes[gid].append(node)
        else:
            extra_groups.setdefault(gid, []).append(node)

    # Build ordered subnet list (only non-empty groups)
    subnets: list[dict] = []
    for i, gid in enumerate(group_order):
        nodes = group_nodes[gid]
        if nodes:
            subnets.append({
                "id":    gid,
                "label": group_labels.get(gid, gid.replace("_", " ").title()),
                "tier":  _tier(i, len(group_order)),
                "nodes": nodes,
            })
    for gid, nodes in extra_groups.items():
        if nodes:
            subnets.append({
                "id":    gid,
                "label": gid.replace("_", " ").title(),
                "tier":  "app",
                "nodes": nodes,
            })

    # ── Gateways ───────────────────────────────────────────────────────────────
    gateways: list[dict] = []
    for p in gateway_placements:
        item  = items_by_id.get(p.id)
        label = item.label if item else p.id
        gateways.append({"id": p.id, "type": p.oci_type, "label": label})

    # ── External items ─────────────────────────────────────────────────────────
    external: list[dict] = []
    for p in external_placements:
        item  = items_by_id.get(p.id)
        label = item.label if item else p.id
        external.append({"id": p.id, "type": p.oci_type, "label": label})

    # ── OCI platform services (right column inside region) ─────────────────────
    oci_services: list[dict] = []
    for p in service_placements:
        item  = items_by_id.get(p.id)
        label = item.label if item else p.id
        oci_services.append({"id": p.id, "type": p.oci_type, "label": label})

    # ── Deployment type ────────────────────────────────────────────────────────
    n_ads     = max(1, hints.availability_domains_per_region)
    n_regions = max(1, hints.region_count)

    # ── Detect multi-environment BOM (items tagged with env names) ────────────
    # bom_parser tags each item's .notes with the section name (Prod, DR, Dev…).
    # When multiple env tags are present, build one region per environment so
    # they appear as separate OCI Compartments instead of mixed subnets.
    env_names: list[str] = list(dict.fromkeys(
        items_by_id[p.id].notes
        for p in subnet_placements
        if p.id in items_by_id and items_by_id[p.id].notes not in _RESERVED_NOTES
    ))
    is_multi_env = len(env_names) > 1

    if is_multi_env:
        # Compartments are IAM isolation boundaries within ONE OCI region — not separate
        # regions.  Produce a single outer region that contains one compartment per
        # environment plus an optional Shared Services compartment for platform services.
        deployment_type = "multi_compartment"
        final_regions = [_build_compartment_region(
            env_names, subnet_placements, items_by_id,
            group_order, group_labels, gateways, oci_services,
        )]
    else:
        if n_regions > 1:
            deployment_type = "multi_region"
        elif n_ads > 1:
            deployment_type = "multi_ad"
        else:
            deployment_type = "single_ad"

        # ── Build AD boxes ─────────────────────────────────────────────────────
        ads: list[dict] = []
        for ad_i in range(n_ads):
            ad: dict = {
                "id":    f"ad{ad_i + 1}_box",
                "label": f"Availability Domain {ad_i + 1}",
            }
            if deployment_type == "single_ad":
                ad["fault_domains"] = [
                    {"id": f"fd{fd_i + 1}_box", "label": f"Fault Domain {fd_i + 1}", "subnets": []}
                    for fd_i in range(3)
                ]
            ad["subnets"] = subnets if ad_i == 0 else []
            ads.append(ad)

        final_regions = [{
            "id":                   "region_box",
            "label":                "OCI Region",
            "regional_subnets":     [],
            "availability_domains": ads,
            "gateways":             gateways,
            "oci_services":         oci_services,
        }]

    primary_region_id = final_regions[0]["id"] if final_regions else "region_box"

    # Collect subnet IDs from both old (availability_domains) and new (compartments) structures
    all_subnet_ids: set[str] = set()
    for r in final_regions:
        for ad in r.get("availability_domains", []):
            for s in ad.get("subnets", []):
                all_subnet_ids.add(s["id"])
        for comp in r.get("compartments", []):
            for s in comp.get("subnets", []):
                all_subnet_ids.add(s["id"])

    # ── Edge ID set for deduplication ─────────────────────────────────────────
    all_node_ids  = {p.id  for p in intent.placements}
    all_group_ids = all_subnet_ids
    all_ad_ids    = (
        {a["id"] for r in final_regions for a in r.get("availability_domains", [])}
        | {comp["id"] for r in final_regions for comp in r.get("compartments", [])}
    )
    all_ids = all_node_ids | all_group_ids | all_ad_ids | {primary_region_id}

    # ── Build edges ────────────────────────────────────────────────────────────
    edges: list[dict] = []
    existing_pairs: set[tuple[str, str]] = set()

    def _add(eid: str, src: str, tgt: str, label: str,
             ex: float = 0.5, ey: float = 1.0,
             nx: float = 0.5, ny: float = 0.0) -> None:
        if (src, tgt) in existing_pairs:
            return
        if src in all_ids and tgt in all_ids:
            edges.append({
                "id": eid, "source": src, "target": tgt, "label": label,
                "exitX": ex, "exitY": ey, "entryX": nx, "entryY": ny,
            })
            existing_pairs.add((src, tgt))

    # Data-flow edges: use LLM-declared edges, or fall back to sequential chain
    if intent.edges:
        for e in intent.edges:
            # Drop edges where source/target is a subnet container box.
            if e.source in all_group_ids or e.target in all_group_ids:
                continue
            _add(e.id, e.source, e.target, e.label)
    else:
        # Sequential chain across subnets of the FIRST environment / primary region
        if deployment_type == "multi_compartment":
            first_comp = (final_regions[0].get("compartments") or [{}])[0]
            sub_ids = [s["id"] for s in first_comp.get("subnets", [])]
        else:
            first_ad = (final_regions[0].get("availability_domains") or [{}])[0]
            sub_ids  = [s["id"] for s in first_ad.get("subnets", [])]
        for i in range(len(sub_ids) - 1):
            _add(f"e_{sub_ids[i]}_{sub_ids[i+1]}", sub_ids[i], sub_ids[i+1], "")

    # Structural edges — always point to the primary region
    igw_id = next((p.id for p in intent.placements if p.oci_type == "internet gateway"), None)
    llm_sources = {e.source for e in intent.edges} if intent.edges else set()

    if "on_prem" in all_node_ids and "on_prem" not in llm_sources:
        conn_label = _CONN_LABELS.get(hints.on_prem_connectivity, "Private Link")
        _add("e_on_prem_region", "on_prem", primary_region_id, conn_label,
             ex=1.0, ey=0.5, nx=0.0, ny=0.5)

    if igw_id and igw_id not in llm_sources:
        _add("e_igw_region", igw_id, primary_region_id, "Internet",
             ex=0.5, ey=1.0, nx=0.5, ny=0.0)

    return {
        "deployment_type": deployment_type,
        "regions":         final_regions,
        "external":        external,
        "edges":           edges,
    }


def _default_group(layer: str, group_order: list[str]) -> str:
    """Map a layer name to the default group when the placement has no explicit group."""
    if not group_order:
        return "app_sub_box"
    if layer == "ingress":
        return group_order[0]
    if layer == "data":
        return group_order[-1]
    # compute, async → middle group
    return group_order[len(group_order) // 2]


def _tier(index: int, total: int) -> str:
    """Return the draw.io tier label (ingress/app/db) for a group at position index/total."""
    if index == 0:
        return "ingress"
    if index == total - 1:
        return "db"
    return "app"


def _build_compartment_region(
    env_names: list[str],
    subnet_placements: list,
    items_by_id: dict,
    group_order: list[str],
    group_labels: dict[str, str],
    gateways: list[dict],
    oci_services: list[dict],
) -> dict:
    """Build a single OCI region containing one compartment per environment.

    Each environment becomes an IAM compartment (not a separate region) with its
    own VCN and 3-tier subnets.  Gateways belong to the first compartment.
    OCI platform services go in a Shared Services section of the outer region.
    """
    compartments: list[dict] = []

    for env_idx, env_name in enumerate(env_names):
        env_ids = {p.id for p in subnet_placements
                   if p.id in items_by_id
                   and items_by_id[p.id].notes == env_name}
        env_placements = [p for p in subnet_placements if p.id in env_ids]

        # Assign nodes to groups, normalising env-prefixed group IDs
        env_prefix = f"{env_name.lower()[:8].replace(' ', '_')}_"
        group_nodes: dict[str, list[dict]] = {gid: [] for gid in group_order}
        extra_nodes: dict[str, list[dict]] = {}

        for p in env_placements:
            item  = items_by_id.get(p.id)
            label = item.label if item else p.id
            node  = {"id": p.id, "type": p.oci_type, "label": label}
            gid   = p.group or _default_group(p.layer, group_order)
            # Strip env prefix if LLM echoed it back (e.g. "prod_app_sub_box")
            if gid not in group_nodes:
                stripped = gid[len(env_prefix):] if gid.startswith(env_prefix) else gid
                gid = stripped if stripped in group_nodes else gid
            if gid in group_nodes:
                group_nodes[gid].append(node)
            else:
                extra_nodes.setdefault(gid, []).append(node)

        # Build flat subnet list for this compartment
        env_subnets: list[dict] = []
        for i, gid in enumerate(group_order):
            nodes = group_nodes[gid]
            if nodes:
                env_subnets.append({
                    "id":    f"e{env_idx}_{gid}",
                    "label": group_labels.get(gid, gid.replace("_", " ").title()),
                    "tier":  _tier(i, len(group_order)),
                    "nodes": nodes,
                })
        for gid, nodes in extra_nodes.items():
            if nodes:
                env_subnets.append({
                    "id":    f"e{env_idx}_{gid}",
                    "label": gid.replace("_", " ").title(),
                    "tier":  "app",
                    "nodes": nodes,
                })

        compartments.append({
            "id":       f"comp_{env_idx}_box",
            "label":    f"{env_name} Compartment",
            "gateways": gateways if env_idx == 0 else [],
            "subnets":  env_subnets,
        })

    return {
        "id":               "region_box",
        "label":            "OCI Region",
        "compartments":     compartments,
        "shared_services":  oci_services,  # platform services in their own section
        # Keep these empty so single-region fallback paths don't see stale data:
        "regional_subnets":     [],
        "availability_domains": [],
        "gateways":             [],
        "oci_services":         [],
    }

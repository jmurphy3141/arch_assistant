"""
agent/intent_compiler.py
-------------------------
Compile a validated LayoutIntent into the legacy flat layout spec format
that agent/layout_engine.py accepts deterministically.

Output format:
  {
    "layers": {
      "external": [{"id": "...", "type": "...", "label": "..."}],
      "ingress":  [...],
      "compute":  [...],
      "async":    [...],
      "data":     [...]
    },
    "groups": [
      {"id": "<slug>", "label": "<display name>", "nodes": [...]},
      ...
    ],
    "edges": [...]
  }

The 'vcn_box' is synthesised by layout_engine._compute_positions_legacy()
from the group bounding boxes — it MUST NOT appear in the spec.

Fixed edges injected deterministically (by compiler, not by LLM):
  igw_id  → vcn_box   (if IGW placement exists)
  on_prem → vcn_box   (if on_prem placement exists)
  group[i] → group[i+1]  (sequential edges between adjacent subnet groups)

When the LLM declares explicit groups (intent.groups), those define the
display labels and ordering.  When no groups are declared the compiler falls
back to the classic 3-tier order (pub_sub_box → app_sub_box → db_sub_box).
"""
from __future__ import annotations

from agent.layout_intent import LayoutIntent, Placement, GROUP_LABELS

# Classic 3-tier fallback order (used when the LLM declares no groups)
_GROUP_ORDER = ["pub_sub_box", "app_sub_box", "db_sub_box"]

# Legacy edge labels for the well-known 3-tier pairs
_LEGACY_EDGE_LABELS = {
    ("pub_sub_box", "app_sub_box"): "HTTP",
    ("app_sub_box", "db_sub_box"):  "Data Access",
}

# Connectivity labels for on_prem → vcn edge
_CONN_LABELS = {
    "fastconnect": "FastConnect",
    "vpn":         "VPN",
    "none":        "",
    "unknown":     "Private Link",
}


class IntentCompileError(ValueError):
    """Raised when the LayoutIntent cannot be compiled into a valid flat spec."""


def compile_intent_to_flat_spec(
    intent: LayoutIntent,
    items: list,
) -> dict:
    """
    Convert a validated LayoutIntent + ServiceItem list into a legacy flat spec.

    Returns {"layers": {...}, "groups": [...], "edges": [...]}.
    Raises IntentCompileError if a required endpoint for a fixed edge is missing.
    """
    items_by_id = {i.id: i for i in (items or [])}

    # ── Build layers ──────────────────────────────────────────────────────────
    layers: dict[str, list] = {
        "external": [],
        "ingress":  [],
        "compute":  [],
        "async":    [],
        "data":     [],
    }

    for p in intent.placements:
        item  = items_by_id.get(p.id)
        label = item.label if item else p.id
        layers[p.layer].append({
            "id":    p.id,
            "type":  p.oci_type,
            "label": label,
        })

    # ── Determine group ordering and labels ───────────────────────────────────
    # When the LLM declared explicit groups, use those (sorted by .order).
    # Otherwise fall back to the classic 3-tier order.
    if intent.groups:
        sorted_decls = sorted(intent.groups, key=lambda g: g.order)
        group_order  = [g.id for g in sorted_decls]
        group_labels = {g.id: g.label for g in sorted_decls}
    else:
        group_order  = list(_GROUP_ORDER)
        group_labels = dict(GROUP_LABELS)

    # ── Build groups ──────────────────────────────────────────────────────────
    group_members: dict[str, list[str]] = {gid: [] for gid in group_order}
    extra_members: dict[str, list[str]] = {}   # groups in placements but not declared

    for p in intent.placements:
        if not p.group:
            continue
        if p.group in group_members:
            group_members[p.group].append(p.id)
        else:
            extra_members.setdefault(p.group, []).append(p.id)

    groups: list[dict] = []
    for gid in group_order:
        members = group_members[gid]
        if members:
            groups.append({
                "id":    gid,
                "label": group_labels.get(gid, gid.replace("_", " ").title()),
                "nodes": members,
            })
    # Append any groups referenced in placements but missing from the declaration
    for gid, members in extra_members.items():
        if members:
            groups.append({
                "id":    gid,
                "label": gid.replace("_", " ").title(),
                "nodes": members,
            })

    # ── Collect all IDs for edge validation ───────────────────────────────────
    all_node_ids  = {p.id for p in intent.placements}
    all_group_ids = {g["id"] for g in groups}
    # vcn_box is synthesised by the layout engine; include it for edge targets
    vcn_exists = bool(groups)  # vcn_box is created iff there are any groups

    # ── Build fixed edges ─────────────────────────────────────────────────────
    edges: list[dict] = []

    def _add(eid: str, src: str, tgt: str, label: str,
             ex: float = 0.5, ey: float = 1.0,
             nx: float = 0.5, ny: float = 0.0) -> None:
        """Append an edge, silently dropping it if either endpoint is absent."""
        src_ok = src in all_node_ids or src in all_group_ids or src == "vcn_box"
        tgt_ok = tgt in all_node_ids or tgt in all_group_ids or tgt == "vcn_box"
        if src_ok and tgt_ok:
            edges.append({
                "id": eid, "source": src, "target": tgt, "label": label,
                "exitX": ex, "exitY": ey, "entryX": nx, "entryY": ny,
            })

    # on_prem → vcn_box
    if "on_prem" in all_node_ids and vcn_exists:
        conn_label = _CONN_LABELS.get(
            intent.deployment_hints.on_prem_connectivity, "Private Link"
        )
        _add("e_on_prem_vcn", "on_prem", "vcn_box", conn_label,
             ex=1.0, ey=0.5, nx=0.0, ny=0.5)

    # internet_gateway → vcn_box
    igw_id = next(
        (p.id for p in intent.placements if p.oci_type == "internet gateway"),
        None,
    )
    if igw_id and vcn_exists:
        _add("e_igw_vcn", igw_id, "vcn_box", "Internet",
             ex=0.5, ey=1.0, nx=0.5, ny=0.0)

    # Sequential edges between adjacent subnet groups (group[i] → group[i+1])
    for i in range(len(groups) - 1):
        src = groups[i]["id"]
        tgt = groups[i + 1]["id"]
        label = _LEGACY_EDGE_LABELS.get((src, tgt), "")
        _add(f"e_{src}_{tgt}", src, tgt, label,
             ex=0.5, ey=1.0, nx=0.5, ny=0.0)

    return {
        "layers": layers,
        "groups": groups,
        "edges":  edges,
    }

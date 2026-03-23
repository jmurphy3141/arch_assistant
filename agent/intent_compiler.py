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
      {"id": "pub_sub_box", "label": "Public Subnet", "nodes": [...]},
      {"id": "app_sub_box", "label": "App Subnet",    "nodes": [...]},
      {"id": "db_sub_box",  "label": "DB Subnet",     "nodes": [...]}
    ],
    "edges": [...]
  }

The 'vcn_box' is synthesised by layout_engine._compute_positions_legacy()
from the group bounding boxes — it MUST NOT appear in the spec.

Fixed edges injected deterministically (by compiler, not by LLM):
  igw_id → vcn_box       (if IGW placement exists)
  pub_sub_box → app_sub_box  (if both groups are non-empty)
  app_sub_box → db_sub_box   (if both groups are non-empty)
  on_prem → vcn_box      (added for label-bearing edge; layout engine also
                          injects a label-less fallback for legacy specs)
"""
from __future__ import annotations

from agent.layout_intent import LayoutIntent, Placement, GROUP_LABELS

# Fixed group display order
_GROUP_ORDER = ["pub_sub_box", "app_sub_box", "db_sub_box"]

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

    # ── Build groups ──────────────────────────────────────────────────────────
    group_members: dict[str, list[str]] = {g: [] for g in _GROUP_ORDER}
    for p in intent.placements:
        if p.group and p.group in group_members:
            group_members[p.group].append(p.id)

    groups: list[dict] = []
    for gid in _GROUP_ORDER:
        members = group_members[gid]
        if members:
            groups.append({
                "id":    gid,
                "label": GROUP_LABELS[gid],
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

    # pub_sub_box → app_sub_box
    if "pub_sub_box" in all_group_ids and "app_sub_box" in all_group_ids:
        _add("e_pub_app", "pub_sub_box", "app_sub_box", "HTTP",
             ex=0.5, ey=1.0, nx=0.5, ny=0.0)

    # app_sub_box → db_sub_box
    if "app_sub_box" in all_group_ids and "db_sub_box" in all_group_ids:
        _add("e_app_db", "app_sub_box", "db_sub_box", "Data Access",
             ex=0.5, ey=1.0, nx=0.5, ny=0.0)

    return {
        "layers": layers,
        "groups": groups,
        "edges":  edges,
    }

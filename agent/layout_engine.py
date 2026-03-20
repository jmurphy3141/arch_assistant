"""
agent/layout_engine.py
-----------------------
Converts LLM layout spec JSON → deterministic x,y positions for every node.

Rules (deterministic, no creativity):
  - Direction: left → right
  - Layer columns: fixed X positions
  - Node rows: stacked top-to-bottom within each layer, sorted by group then id
  - Groups: bounding rect of member nodes + padding
  - All positions are ABSOLUTE (page coordinates)
  - Page: Landscape A3 1654×1169
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Layout constants ──────────────────────────────────────────────────────────
PAGE_W      = 1654
PAGE_H      = 1169
MARGIN      = 50         # page margin

ICON_W      = 48         # icon render width
ICON_H      = 48         # icon render height
LABEL_H     = 20         # label below icon
ICON_TOTAL  = ICON_H + LABEL_H + 8   # total vertical slot per icon

LAYER_GAP   = 40         # horizontal gap between layers
NODE_GAP_Y  = 24         # vertical gap between nodes in same layer

GROUP_PAD_X = 24         # horizontal padding inside group box
GROUP_PAD_Y = 36         # vertical padding inside group box (top = label space)
GROUP_GAP_Y = 20         # vertical gap between group boxes in same layer

AD_PAD_X    = 16         # extra padding around subnets for AD box
AD_PAD_Y    = 32         # top padding large enough for "Availability Domain 1" label

COMPARTMENT_PAD_X = 36  # padding around VCN+region for Compartment box
COMPARTMENT_PAD_Y = 48  # top padding for "Compartment" label

# Compute layer column centres based on available width
# Layers: external | ingress | compute | async | data | region
# "region" is a virtual 6th column used for OCI Region Services box,
# keeping it separate from DB Subnet so vcn_box → region_box arrow stays forward.
N_LAYERS   = 6
LAYER_W    = (PAGE_W - 2 * MARGIN - (N_LAYERS - 1) * LAYER_GAP) / N_LAYERS
LAYER_CENTRES = {
    "external": MARGIN + LAYER_W * 0 + LAYER_GAP * 0 + LAYER_W / 2,
    "ingress":  MARGIN + LAYER_W * 1 + LAYER_GAP * 1 + LAYER_W / 2,
    "compute":  MARGIN + LAYER_W * 2 + LAYER_GAP * 2 + LAYER_W / 2,
    "async":    MARGIN + LAYER_W * 3 + LAYER_GAP * 3 + LAYER_W / 2,
    "data":     MARGIN + LAYER_W * 4 + LAYER_GAP * 4 + LAYER_W / 2,
    "region":   MARGIN + LAYER_W * 5 + LAYER_GAP * 5 + LAYER_W / 2,
}

# X left edge of each layer column
LAYER_X = {k: v - LAYER_W / 2 for k, v in LAYER_CENTRES.items()}


@dataclass
class PositionedNode:
    id:       str
    label:    str
    oci_type: str
    layer:    str
    group_id: str | None   # group box this node belongs to
    x:        float        # absolute page x (left of icon)
    y:        float        # absolute page y (top of icon)
    w:        float = ICON_W
    h:        float = ICON_TOTAL
    is_group_box: bool = False   # True for background rectangle nodes


@dataclass
class PositionedGroup:
    id:    str
    label: str
    x:     float
    y:     float
    w:     float
    h:     float


def compute_positions(layout_spec: dict | str) -> tuple[list[PositionedNode], list[PositionedGroup]]:
    """
    Convert layout spec JSON → absolute positions.
    Returns (nodes, groups) both in page coordinates.
    """
    if isinstance(layout_spec, str):
        layout_spec = json.loads(layout_spec)

    layers: dict[str, list[dict]] = layout_spec.get("layers", {})
    groups_spec: list[dict]       = layout_spec.get("groups", [])

    # Build group membership: node_id → group_id
    node_to_group: dict[str, str] = {}
    for g in groups_spec:
        for nid in g.get("nodes", []):
            node_to_group[nid] = g["id"]

    # Build node lookup: id → spec dict
    all_nodes: dict[str, dict] = {}
    for layer_name, node_list in layers.items():
        for n in node_list:
            all_nodes[n["id"]] = {**n, "layer": layer_name}

    # ── Place nodes layer by layer ────────────────────────────────────────────
    positioned: list[PositionedNode] = []

    LAYER_ORDER = ["external", "ingress", "compute", "async", "data"]

    for layer_name in LAYER_ORDER:
        node_list = layers.get(layer_name, [])
        if not node_list:
            continue

        lx = LAYER_X.get(layer_name, MARGIN)
        cx = LAYER_CENTRES.get(layer_name, MARGIN + LAYER_W / 2)

        # Sort: group members first (sorted by group_id), then ungrouped
        def sort_key(n):
            gid = node_to_group.get(n["id"], "zzz")
            return (gid, n["id"])

        node_list_sorted = sorted(node_list, key=sort_key)

        # Track Y position, resetting per-group for group box calculation
        cur_y = MARGIN + GROUP_PAD_Y
        current_group = None
        group_start_y: dict[str, float] = {}
        group_end_y:   dict[str, float] = {}

        for n in node_list_sorted:
            nid     = n["id"]
            label   = n.get("label", nid)
            ntype   = n.get("type", "")
            gid     = node_to_group.get(nid)

            # Add gap when switching groups
            if gid != current_group:
                if current_group is not None:
                    # region_box lives in its own column — reset Y to page top
                    # so it aligns vertically with the rest of the diagram.
                    if gid == "region_box":
                        cur_y = MARGIN + GROUP_PAD_Y
                    else:
                        cur_y += GROUP_GAP_Y
                group_start_y.setdefault(gid, cur_y) if gid else None
                current_group = gid

            # region_box nodes use the dedicated 6th "region" column
            if gid == "region_box":
                ix = LAYER_CENTRES["region"] - ICON_W / 2
            else:
                ix = cx - ICON_W / 2

            positioned.append(PositionedNode(
                id=nid, label=label, oci_type=ntype,
                layer=layer_name, group_id=gid,
                x=ix, y=cur_y,
                w=ICON_W, h=ICON_TOTAL,
            ))

            if gid:
                group_end_y[gid] = cur_y + ICON_TOTAL

            cur_y += ICON_TOTAL + NODE_GAP_Y

    # ── Compute subnet group bounding boxes ───────────────────────────────────
    group_boxes: list[PositionedGroup] = []

    for g in groups_spec:
        gid   = g["id"]
        label = g["label"]
        members = [p for p in positioned if p.group_id == gid]
        if not members:
            continue

        # Size to fit contents — no padding to full layer width
        min_x = min(p.x for p in members) - GROUP_PAD_X
        max_x = max(p.x + p.w for p in members) + GROUP_PAD_X
        min_y = min(p.y for p in members) - GROUP_PAD_Y
        max_y = max(p.y + p.h for p in members) + GROUP_PAD_Y / 2

        group_boxes.append(PositionedGroup(
            id=gid, label=label,
            x=min_x, y=min_y,
            w=max_x - min_x, h=max_y - min_y,
        ))

    # ── VCN box — wraps Public Subnet + App Subnet + DB Subnet ────────────────
    VCN_SUBNET_GROUPS = {"pub_sub_box", "app_sub_box", "db_sub_box"}
    LEFT_GATEWAYS     = {"internet gateway", "nat gateway", "drg"}
    RIGHT_GATEWAYS    = {"service gateway"}

    vcn_subnet_boxes  = [g for g in group_boxes if g.id in VCN_SUBNET_GROUPS]

    if vcn_subnet_boxes:
        vcn_x = min(g.x for g in vcn_subnet_boxes) - GROUP_PAD_X
        vcn_y = min(g.y for g in vcn_subnet_boxes) - GROUP_PAD_Y
        vcn_r = max(g.x + g.w for g in vcn_subnet_boxes) + GROUP_PAD_X
        vcn_b = max(g.y + g.h for g in vcn_subnet_boxes) + GROUP_PAD_Y / 2
        vcn_w = vcn_r - vcn_x
        vcn_h = vcn_b - vcn_y

        # Override gateway X to straddle the VCN border
        for p in positioned:
            if p.oci_type in LEFT_GATEWAYS:
                p.x = vcn_x - ICON_W / 2     # straddle left edge
            elif p.oci_type in RIGHT_GATEWAYS:
                p.x = vcn_r - ICON_W / 2     # straddle right edge

        # ── AD box — wraps subnet boxes inside the VCN ────────────────────────
        ad_x = min(g.x for g in vcn_subnet_boxes) - AD_PAD_X
        ad_y = min(g.y for g in vcn_subnet_boxes) - AD_PAD_Y
        ad_r = max(g.x + g.w for g in vcn_subnet_boxes) + AD_PAD_X
        ad_b = max(g.y + g.h for g in vcn_subnet_boxes) + AD_PAD_X / 2
        ad_box = PositionedGroup(
            id="ad_box", label="Availability Domain 1",
            x=ad_x, y=ad_y,
            w=ad_r - ad_x, h=ad_b - ad_y,
        )

        vcn_box = PositionedGroup(
            id="vcn_box", label="VCN",
            x=vcn_x, y=vcn_y,
            w=vcn_w, h=vcn_h,
        )

        # ── Compartment box — wraps VCN + Region Services ─────────────────────
        region_pg = next((g for g in group_boxes if g.id == "region_box"), None)
        comp_candidates = [vcn_box]
        if region_pg:
            comp_candidates.append(region_pg)
        comp_x = min(g.x for g in comp_candidates) - COMPARTMENT_PAD_X
        comp_y = min(g.y for g in comp_candidates) - COMPARTMENT_PAD_Y
        comp_r = max(g.x + g.w for g in comp_candidates) + COMPARTMENT_PAD_X
        comp_b = max(g.y + g.h for g in comp_candidates) + COMPARTMENT_PAD_X / 2
        compartment_box = PositionedGroup(
            id="compartment_box", label="Compartment",
            x=comp_x, y=comp_y,
            w=comp_r - comp_x, h=comp_b - comp_y,
        )

        # Draw order (front of list = drawn first = furthest back):
        # Compartment → VCN → AD → subnets/region (already in group_boxes)
        group_boxes.insert(0, compartment_box)
        group_boxes.insert(1, vcn_box)
        group_boxes.insert(2, ad_box)

    return positioned, group_boxes


def spec_to_draw_dict(
    layout_spec: dict | str,
    items_by_id: dict[str, object],          # ServiceItem lookup by id
) -> dict:
    """
    Convert layout spec + ServiceItems → a dict that drawio_generator.py accepts.
    Returns {"nodes": [...], "edges": [...]} in the format the generator expects.
    """
    if isinstance(layout_spec, str):
        layout_spec = json.loads(layout_spec)

    nodes_out, groups_out = compute_positions(layout_spec)
    edges_spec = layout_spec.get("edges", [])

    # Build draw dict
    draw_nodes = []
    draw_edges = []

    # Add group boxes first (drawn behind icons)
    for g in groups_out:
        draw_nodes.append({
            "id":    g.id,
            "type":  "_group_box",      # special type — generator renders as styled rect
            "label": g.label,
            "x":     g.x,
            "y":     g.y,
            "w":     g.w,
            "h":     g.h,
        })

    # Add icon nodes
    for n in nodes_out:
        item = items_by_id.get(n.id)
        draw_nodes.append({
            "id":       n.id,
            "type":     n.oci_type,
            "label":    n.label,
            "x":        n.x,
            "y":        n.y,
            "w":        n.w,
            "h":        n.h,
            "layer":    n.layer,
            "group_id": n.group_id,
        })

    # Add edges — all at subnet/VCN boundaries with dynamic entry Y
    # so arrows land at the exact gateway icon level on the VCN edge
    node_by_id = {n.id: n for n in nodes_out}
    vcn_box    = next((g for g in groups_out if g.id == "vcn_box"), None)

    def entry_y_frac(icon_id: str, box) -> float:
        """Y fraction (0-1) where arrow enters box, vertically aligned to icon."""
        if not box:
            return 0.5
        n = node_by_id.get(icon_id)
        if not n:
            return 0.5
        frac = (n.y + n.h / 2 - box.y) / box.h
        return round(max(0.05, min(0.95, frac)), 3)

    FIXED_EDGES = [
        # (source,        target,        label,            exitY_icon,        entryY_icon)
        # internet_gateway is positioned straddling the VCN left border — its visual
        # placement already conveys the connection, so no separate arrow is needed.
        ("on_prem",       "vcn_box",     "FastConnect ×6", None,              "drg_1"),
        ("pub_sub_box",   "app_sub_box", "LB Traffic",     None,              None),
        ("app_sub_box",   "db_sub_box",  "Data Access",    None,              None),
        ("vcn_box",       "region_box",  "",               "service_gateway", None),
    ]

    group_by_id = {g.id: g for g in groups_out}

    for src, tgt, lbl, exit_icon, entry_icon in FIXED_EDGES:
        src_box = group_by_id.get(src)
        tgt_box = group_by_id.get(tgt)

        exit_y  = entry_y_frac(exit_icon,  src_box) if exit_icon  else 0.5
        entry_y = entry_y_frac(entry_icon, tgt_box) if entry_icon else 0.5

        draw_edges.append({
            "id":     f"e_{src}_{tgt}",
            "source": src,
            "target": tgt,
            "label":  lbl,
            "exitX":  1.0,
            "exitY":  exit_y,
            "entryX": 0.0,
            "entryY": entry_y,
        })

    return {"nodes": draw_nodes, "edges": draw_edges}

"""
agent/layout_engine.py
-----------------------
Converts LLM layout spec JSON → deterministic x,y positions for every node.

New spec format (hierarchical, top-to-bottom):
  deployment_type: "single_ad" | "multi_ad" | "multi_region"
  regions[] → availability_domains[] → fault_domains[] → subnets[] → nodes[]
  regions[] → regional_subnets[]      (drawn above AD boxes inside region)
  regions[] → gateways[]              (IGW top, DRG left, NAT/SGW right)
  regions[] → oci_services[]          (right side outside region)
  external[]                          (left side outside region + top)
  edges[]

Rules (deterministic, no creativity):
  - Direction: top → bottom (TB)
  - Regional subnets at top inside region, AD boxes below
  - Nodes in horizontal rows inside each subnet
  - All positions are ABSOLUTE (page coordinates)
  - Page: Landscape A3 1654×1169
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Canvas constants ───────────────────────────────────────────────────────────
PAGE_W, PAGE_H = 1654, 1169
PAGE_MARGIN = 30
EXT_W    = 90    # external element column width
EXT_GAP  = 24    # gap between external column and region box
EXT_TOP_H = 90   # strip above region for internet/admin elements

ICON_W, ICON_H = 48, 48
LABEL_H  = 20
ICON_SLOT = ICON_H + LABEL_H + 8   # 76px vertical per icon including label
NODE_GAP_X = 14

SUB_PAD_X = 14; SUB_PAD_TOP = 32; SUB_PAD_BOT = 10; SUB_GAP_Y = 12
MIN_SUBNET_W = 200   # minimum subnet box width — prevents single-icon subnets from being too narrow
FD_PAD_X  = 12; FD_PAD_TOP  = 32; FD_PAD_BOT  = 12; FD_GAP_X  = 12
AD_PAD_X  = 14; AD_PAD_TOP  = 36; AD_PAD_BOT  = 14; AD_GAP_X  = 20
REG_PAD_X = 20; REG_PAD_TOP = 40; REG_PAD_BOT = 20; REG_SUB_GAP_Y = 14
MULTI_REGION_GAP = 30

# Single-region position constants
REGION_X = PAGE_MARGIN + EXT_W + EXT_GAP          # 144
REGION_W = PAGE_W - 2 * (PAGE_MARGIN + EXT_W + EXT_GAP)  # 1366
REGION_Y = PAGE_MARGIN + EXT_TOP_H                # 120


@dataclass
class PositionedBox:
    id: str
    label: str
    box_type: str   # _region_box | _ad_box | _fd_box | _subnet_box
    tier: str = ""  # for subnet: ingress | web | app | db
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0


@dataclass
class PositionedNode:
    id: str
    label: str
    oci_type: str
    x: float = 0
    y: float = 0
    w: float = ICON_W
    h: float = ICON_SLOT


def _subnet_content_size(subnet_spec: dict) -> tuple[float, float]:
    """Return (w, h) of the content area for one subnet (nodes in a single row)."""
    nodes = subnet_spec.get("nodes", [])
    n = max(len(nodes), 1)
    content_w = n * ICON_W + (n - 1) * NODE_GAP_X
    content_h = ICON_SLOT
    return content_w, content_h


def _subnet_box_size(subnet_spec: dict) -> tuple[float, float]:
    """Return (w, h) of the subnet box including padding, respecting MIN_SUBNET_W."""
    cw, ch = _subnet_content_size(subnet_spec)
    return max(cw + 2 * SUB_PAD_X, MIN_SUBNET_W), ch + SUB_PAD_TOP + SUB_PAD_BOT


def _place_subnet_nodes(subnet_spec: dict, box_x: float, box_y: float) -> list[PositionedNode]:
    """Place nodes in a horizontal row centred inside the subnet box."""
    nodes_spec = subnet_spec.get("nodes", [])
    if not nodes_spec:
        return []

    n = len(nodes_spec)
    total_w = n * ICON_W + (n - 1) * NODE_GAP_X
    box_w, _ = _subnet_box_size(subnet_spec)
    start_x = box_x + (box_w - total_w) / 2
    node_y = box_y + SUB_PAD_TOP

    result = []
    for i, nd in enumerate(nodes_spec):
        result.append(PositionedNode(
            id=nd["id"],
            label=nd.get("label", nd["id"]),
            oci_type=nd.get("type", ""),
            x=start_x + i * (ICON_W + NODE_GAP_X),
            y=node_y,
            w=ICON_W,
            h=ICON_SLOT,
        ))
    return result


def _layout_subnets_vertical(
    subnets: list[dict],
    origin_x: float,
    origin_y: float,
    available_w: float,
) -> tuple[list[PositionedBox], list[PositionedNode], float]:
    """
    Stack subnet boxes vertically. Each box uses its natural (content-based) width,
    centred within available_w. Prevents single-icon subnets from stretching to fill
    the full column.
    Returns (boxes, nodes, total_height_used).
    """
    boxes: list[PositionedBox] = []
    nodes: list[PositionedNode] = []
    cur_y = origin_y

    for sub in subnets:
        sub_w, sub_h = _subnet_box_size(sub)
        # Centre within the available column; clamp so it never exceeds available_w
        sub_w = min(sub_w, available_w)
        sub_x = origin_x + (available_w - sub_w) / 2

        tier = sub.get("tier", "app")
        boxes.append(PositionedBox(
            id=sub["id"],
            label=sub.get("label", sub["id"]),
            box_type="_subnet_box",
            tier=tier,
            x=sub_x,
            y=cur_y,
            w=sub_w,
            h=sub_h,
        ))
        nodes.extend(_place_subnet_nodes(sub, sub_x, cur_y))
        cur_y += sub_h + SUB_GAP_Y

    total_h = cur_y - origin_y - SUB_GAP_Y if subnets else 0
    return boxes, nodes, total_h


def _layout_subnets_horizontal(
    subnets: list[dict],
    origin_x: float,
    origin_y: float,
    available_w: float,
) -> tuple[list[PositionedBox], list[PositionedNode], float]:
    """
    Place subnet boxes side by side horizontally (for regional subnets).
    Each box uses its natural (content-based) width; the row is centred in available_w.
    Returns (boxes, nodes, max_height_used).
    """
    boxes: list[PositionedBox] = []
    nodes: list[PositionedNode] = []
    if not subnets:
        return boxes, nodes, 0

    n = len(subnets)
    widths = [_subnet_box_size(sub)[0] for sub in subnets]
    total_natural_w = sum(widths) + (n - 1) * SUB_GAP_Y

    # Centre row; if natural widths exceed available_w scale down proportionally
    if total_natural_w > available_w:
        scale = available_w / total_natural_w
        widths = [w * scale for w in widths]
        total_natural_w = available_w

    start_x = origin_x + (available_w - total_natural_w) / 2
    max_h = 0
    cur_x = start_x

    for sub, sub_w in zip(subnets, widths):
        _, sub_h = _subnet_box_size(sub)
        tier = sub.get("tier", "ingress")

        boxes.append(PositionedBox(
            id=sub["id"],
            label=sub.get("label", sub["id"]),
            box_type="_subnet_box",
            tier=tier,
            x=cur_x,
            y=origin_y,
            w=sub_w,
            h=sub_h,
        ))
        nodes.extend(_place_subnet_nodes(sub, cur_x, origin_y))
        max_h = max(max_h, sub_h)
        cur_x += sub_w + SUB_GAP_Y

    return boxes, nodes, max_h


def _layout_fd(
    fd_spec: dict,
    origin_x: float,
    origin_y: float,
    available_w: float,
) -> tuple[PositionedBox, list[PositionedBox], list[PositionedNode]]:
    """Layout a single Fault Domain box with its subnets stacked vertically."""
    subnets = fd_spec.get("subnets", [])
    inner_x = origin_x + FD_PAD_X
    inner_y = origin_y + FD_PAD_TOP
    inner_w = available_w - 2 * FD_PAD_X

    sub_boxes, sub_nodes, content_h = _layout_subnets_vertical(subnets, inner_x, inner_y, inner_w)

    fd_h = FD_PAD_TOP + content_h + FD_PAD_BOT
    fd_box = PositionedBox(
        id=fd_spec["id"],
        label=fd_spec.get("label", fd_spec["id"]),
        box_type="_fd_box",
        x=origin_x,
        y=origin_y,
        w=available_w,
        h=fd_h,
    )
    return fd_box, sub_boxes, sub_nodes


def _layout_ad_single(
    ad_spec: dict,
    origin_x: float,
    origin_y: float,
    available_w: float,
) -> tuple[PositionedBox, list[PositionedBox], list[PositionedNode]]:
    """
    Layout an AD for single_ad mode: FD boxes side by side, then AD-level subnets below.
    """
    fault_domains = ad_spec.get("fault_domains", [])
    ad_subnets    = ad_spec.get("subnets", [])

    all_boxes: list[PositionedBox] = []
    all_nodes: list[PositionedNode] = []

    cur_y = origin_y + AD_PAD_TOP
    inner_x = origin_x + AD_PAD_X
    inner_w = available_w - 2 * AD_PAD_X

    if fault_domains:
        n_fds = len(fault_domains)
        fd_w = (inner_w - (n_fds - 1) * FD_GAP_X) / n_fds
        max_fd_h = 0

        for i, fd in enumerate(fault_domains):
            fd_x = inner_x + i * (fd_w + FD_GAP_X)
            fd_box, sub_boxes, sub_nodes = _layout_fd(fd, fd_x, cur_y, fd_w)
            all_boxes.append(fd_box)
            all_boxes.extend(sub_boxes)
            all_nodes.extend(sub_nodes)
            max_fd_h = max(max_fd_h, fd_box.h)

        cur_y += max_fd_h + SUB_GAP_Y

    # AD-level subnets (e.g. DB tier spanning both FDs)
    if ad_subnets:
        sub_boxes, sub_nodes, subs_h = _layout_subnets_vertical(ad_subnets, inner_x, cur_y, inner_w)
        all_boxes.extend(sub_boxes)
        all_nodes.extend(sub_nodes)
        cur_y += subs_h + SUB_GAP_Y

    ad_h = cur_y - origin_y + AD_PAD_BOT
    ad_box = PositionedBox(
        id=ad_spec["id"],
        label=ad_spec.get("label", ad_spec["id"]),
        box_type="_ad_box",
        x=origin_x,
        y=origin_y,
        w=available_w,
        h=ad_h,
    )
    return ad_box, all_boxes, all_nodes


def _layout_ad_multi(
    ad_spec: dict,
    origin_x: float,
    origin_y: float,
    available_w: float,
) -> tuple[PositionedBox, list[PositionedBox], list[PositionedNode]]:
    """
    Layout an AD for multi_ad / multi_region mode: subnets stacked vertically, no FDs.
    """
    subnets = ad_spec.get("subnets", [])
    all_boxes: list[PositionedBox] = []
    all_nodes: list[PositionedNode] = []

    inner_x = origin_x + AD_PAD_X
    inner_y = origin_y + AD_PAD_TOP
    inner_w = available_w - 2 * AD_PAD_X

    sub_boxes, sub_nodes, content_h = _layout_subnets_vertical(subnets, inner_x, inner_y, inner_w)
    all_boxes.extend(sub_boxes)
    all_nodes.extend(sub_nodes)

    ad_h = AD_PAD_TOP + content_h + AD_PAD_BOT
    ad_box = PositionedBox(
        id=ad_spec["id"],
        label=ad_spec.get("label", ad_spec["id"]),
        box_type="_ad_box",
        x=origin_x,
        y=origin_y,
        w=available_w,
        h=ad_h,
    )
    return ad_box, all_boxes, all_nodes


def _layout_region(
    region_spec: dict,
    origin_x: float,
    origin_y: float,
    region_w: float,
    deployment_type: str,
) -> tuple[PositionedBox, list[PositionedBox], list[PositionedNode], list[PositionedNode]]:
    """
    Layout a region box with all its contents.
    Returns (region_box, all_sub_boxes, all_icon_nodes, gateway_nodes).
    Gateway nodes are positioned relative to region box edges.
    """
    regional_subnets = region_spec.get("regional_subnets", [])
    ads              = region_spec.get("availability_domains", [])
    gateways         = region_spec.get("gateways", [])
    oci_services     = region_spec.get("oci_services", [])

    all_boxes: list[PositionedBox] = []
    all_nodes: list[PositionedNode] = []

    inner_x = origin_x + REG_PAD_X
    inner_w = region_w - 2 * REG_PAD_X
    cur_y   = origin_y + REG_PAD_TOP

    # 1. Regional subnets — side by side horizontally
    if regional_subnets:
        rsub_boxes, rsub_nodes, rsub_h = _layout_subnets_horizontal(
            regional_subnets, inner_x, cur_y, inner_w
        )
        all_boxes.extend(rsub_boxes)
        all_nodes.extend(rsub_nodes)
        cur_y += rsub_h + REG_SUB_GAP_Y

    # 2. AD boxes — side by side horizontally
    if ads:
        n_ads = len(ads)
        ad_w = (inner_w - (n_ads - 1) * AD_GAP_X) / n_ads
        max_ad_h = 0

        for i, ad in enumerate(ads):
            ad_x = inner_x + i * (ad_w + AD_GAP_X)
            if deployment_type == "single_ad":
                ad_box, sub_boxes, sub_nodes = _layout_ad_single(ad, ad_x, cur_y, ad_w)
            else:
                ad_box, sub_boxes, sub_nodes = _layout_ad_multi(ad, ad_x, cur_y, ad_w)

            all_boxes.append(ad_box)
            all_boxes.extend(sub_boxes)
            all_nodes.extend(sub_nodes)
            max_ad_h = max(max_ad_h, ad_box.h)

        cur_y += max_ad_h + REG_SUB_GAP_Y

    region_h = cur_y - origin_y + REG_PAD_BOT

    region_box = PositionedBox(
        id=region_spec["id"],
        label=region_spec.get("label", region_spec["id"]),
        box_type="_region_box",
        x=origin_x,
        y=origin_y,
        w=region_w,
        h=region_h,
    )

    # 3. Gateway nodes — positioned relative to region box edges
    gateway_nodes: list[PositionedNode] = []
    nat_y = None

    for gw in gateways:
        gtype = gw.get("type", "").lower()
        pos   = gw.get("position", "").lower()

        if gtype == "internet gateway" or pos == "top":
            gx = origin_x + region_w / 2 - ICON_W / 2
            gy = origin_y - ICON_H / 2
        elif gtype == "drg" or pos == "left":
            gx = origin_x - ICON_W / 2
            gy = origin_y + region_h * 0.4
        elif gtype == "nat gateway" or (pos == "right" and nat_y is None):
            gx = origin_x + region_w - ICON_W / 2
            gy = origin_y + region_h * 0.3
            nat_y = gy
        elif pos == "right" or gtype == "service gateway":
            # Service GW below NAT
            gx = origin_x + region_w - ICON_W / 2
            ref_y = nat_y if nat_y is not None else origin_y + region_h * 0.3
            gy = ref_y + ICON_SLOT + 20
        else:
            gx = origin_x + region_w / 2 - ICON_W / 2
            gy = origin_y - ICON_H / 2

        gateway_nodes.append(PositionedNode(
            id=gw["id"],
            label=gw.get("label", gw["id"]),
            oci_type=gtype,
            x=gx,
            y=gy,
            w=ICON_W,
            h=ICON_SLOT,
        ))

    # 4. OCI services — column to the right
    svc_x = origin_x + region_w + EXT_GAP + 20
    svc_y = origin_y + REG_PAD_TOP
    for svc in oci_services:
        all_nodes.append(PositionedNode(
            id=svc["id"],
            label=svc.get("label", svc["id"]),
            oci_type=svc.get("type", ""),
            x=svc_x,
            y=svc_y,
            w=ICON_W,
            h=ICON_SLOT,
        ))
        svc_y += ICON_SLOT + NODE_GAP_X

    return region_box, all_boxes, all_nodes, gateway_nodes


LEFT_EXTERNAL_TYPES = {
    "on premises", "vpn", "fastconnect", "cpe", "dns", "users", "user"
}
TOP_EXTERNAL_TYPES = {
    "internet", "public internet", "admins", "admin", "workstation", "browser"
}


def compute_positions(layout_spec: dict | str) -> tuple[list[PositionedNode], list[PositionedBox]]:
    """
    Convert layout spec JSON → absolute positions.
    Returns (nodes, boxes) both in page coordinates.

    Supports both new hierarchical format (regions/ADs/FDs/subnets) and
    falls back gracefully to old flat format for backward compatibility.
    """
    if isinstance(layout_spec, str):
        layout_spec = json.loads(layout_spec)

    # Detect old flat format (has "layers" key)
    if "layers" in layout_spec and "regions" not in layout_spec:
        return _compute_positions_legacy(layout_spec)

    deployment_type = layout_spec.get("deployment_type", "single_ad")
    regions         = layout_spec.get("regions", [])
    external        = layout_spec.get("external", [])

    all_boxes: list[PositionedBox] = []
    all_nodes: list[PositionedNode] = []

    # ── Place regions ──────────────────────────────────────────────────────────
    n_regions = len(regions)
    if n_regions == 0:
        return [], []

    if n_regions == 1:
        reg_positions = [(REGION_X, REGION_Y, REGION_W)]
    else:
        # Two regions side by side (multi_region)
        single_w = (REGION_W - MULTI_REGION_GAP) / 2
        reg_positions = [
            (REGION_X, REGION_Y, single_w),
            (REGION_X + single_w + MULTI_REGION_GAP, REGION_Y, single_w),
        ]

    for i, region_spec in enumerate(regions):
        rx, ry, rw = reg_positions[i]
        reg_box, sub_boxes, icon_nodes, gw_nodes = _layout_region(
            region_spec, rx, ry, rw, deployment_type
        )
        all_boxes.append(reg_box)
        all_boxes.extend(sub_boxes)
        all_nodes.extend(icon_nodes)
        all_nodes.extend(gw_nodes)

    # ── Place external elements ────────────────────────────────────────────────
    left_ext = [e for e in external if e.get("type", "").lower() in LEFT_EXTERNAL_TYPES]
    top_ext  = [e for e in external if e.get("type", "").lower() in TOP_EXTERNAL_TYPES]
    other_ext = [
        e for e in external
        if e.get("type", "").lower() not in LEFT_EXTERNAL_TYPES
        and e.get("type", "").lower() not in TOP_EXTERNAL_TYPES
    ]
    # Anything unclassified goes left
    left_ext.extend(other_ext)

    left_x = PAGE_MARGIN
    left_y = REGION_Y + REG_PAD_TOP
    for ext in left_ext:
        all_nodes.append(PositionedNode(
            id=ext["id"],
            label=ext.get("label", ext["id"]),
            oci_type=ext.get("type", ""),
            x=left_x,
            y=left_y,
            w=ICON_W,
            h=ICON_SLOT,
        ))
        left_y += ICON_SLOT + NODE_GAP_X

    if top_ext:
        n_top = len(top_ext)
        total_top_w = n_top * ICON_W + (n_top - 1) * NODE_GAP_X
        top_start_x = REGION_X + (REGION_W - total_top_w) / 2
        top_y = PAGE_MARGIN
        for j, ext in enumerate(top_ext):
            all_nodes.append(PositionedNode(
                id=ext["id"],
                label=ext.get("label", ext["id"]),
                oci_type=ext.get("type", ""),
                x=top_start_x + j * (ICON_W + NODE_GAP_X),
                y=top_y,
                w=ICON_W,
                h=ICON_SLOT,
            ))

    return all_nodes, all_boxes


def _compute_positions_legacy(layout_spec: dict) -> tuple[list[PositionedNode], list[PositionedBox]]:
    """
    Backward-compatible layout for old flat spec format (has 'layers' key).
    Wraps old PositionedGroup output as PositionedBox for uniform return type.
    """
    # Import inline to avoid circular issues if ever split
    layers: dict = layout_spec.get("layers", {})
    groups_spec: list = layout_spec.get("groups", [])

    node_to_group: dict[str, str] = {}
    for g in groups_spec:
        for nid in g.get("nodes", []):
            node_to_group[nid] = g["id"]

    LAYER_ORDER = ["external", "ingress", "compute", "async", "data"]

    PAGE_MARGIN_L = 50
    LAYER_GAP_L = 40
    NODE_GAP_Y_L = 24
    GROUP_PAD_X_L = 24
    GROUP_PAD_Y_L = 36
    GROUP_GAP_Y_L = 20
    N_LAYERS = 6
    LAYER_W_L = (PAGE_W - 2 * PAGE_MARGIN_L - (N_LAYERS - 1) * LAYER_GAP_L) / N_LAYERS
    LAYER_CENTRES = {
        "external": PAGE_MARGIN_L + LAYER_W_L * 0 + LAYER_GAP_L * 0 + LAYER_W_L / 2,
        "ingress":  PAGE_MARGIN_L + LAYER_W_L * 1 + LAYER_GAP_L * 1 + LAYER_W_L / 2,
        "compute":  PAGE_MARGIN_L + LAYER_W_L * 2 + LAYER_GAP_L * 2 + LAYER_W_L / 2,
        "async":    PAGE_MARGIN_L + LAYER_W_L * 3 + LAYER_GAP_L * 3 + LAYER_W_L / 2,
        "data":     PAGE_MARGIN_L + LAYER_W_L * 4 + LAYER_GAP_L * 4 + LAYER_W_L / 2,
        "region":   PAGE_MARGIN_L + LAYER_W_L * 5 + LAYER_GAP_L * 5 + LAYER_W_L / 2,
    }

    positioned: list[PositionedNode] = []

    for layer_name in LAYER_ORDER:
        node_list = layers.get(layer_name, [])
        if not node_list:
            continue

        cx = LAYER_CENTRES.get(layer_name, PAGE_MARGIN_L + LAYER_W_L / 2)

        def sort_key(n):
            gid = node_to_group.get(n["id"], "zzz")
            return (gid, n["id"])

        node_list_sorted = sorted(node_list, key=sort_key)
        cur_y = PAGE_MARGIN_L + GROUP_PAD_Y_L
        current_group = None

        for n in node_list_sorted:
            nid   = n["id"]
            label = n.get("label", nid)
            ntype = n.get("type", "")
            gid   = node_to_group.get(nid)

            if gid != current_group:
                if current_group is not None:
                    if gid == "region_box":
                        cur_y = PAGE_MARGIN_L + GROUP_PAD_Y_L
                    else:
                        cur_y += GROUP_GAP_Y_L
                current_group = gid

            if gid == "region_box":
                ix = LAYER_CENTRES["region"] - ICON_W / 2
            else:
                ix = cx - ICON_W / 2

            positioned.append(PositionedNode(
                id=nid, label=label, oci_type=ntype,
                x=ix, y=cur_y, w=ICON_W, h=ICON_SLOT,
            ))
            cur_y += ICON_SLOT + NODE_GAP_Y_L

    # Build group boxes
    group_boxes: list[PositionedBox] = []
    for g in groups_spec:
        gid   = g["id"]
        label = g["label"]
        # filter members that belong to this group — use node_to_group
        members = [p for p in positioned if node_to_group.get(p.id) == gid]
        if not members:
            continue

        min_x = min(p.x for p in members) - GROUP_PAD_X_L
        max_x = max(p.x + p.w for p in members) + GROUP_PAD_X_L
        min_y = min(p.y for p in members) - GROUP_PAD_Y_L
        max_y = max(p.y + p.h for p in members) + GROUP_PAD_Y_L / 2

        group_boxes.append(PositionedBox(
            id=gid, label=label,
            box_type="_subnet_box",
            x=min_x, y=min_y,
            w=max_x - min_x, h=max_y - min_y,
        ))

    # Synthesise a vcn_box that wraps all subnet group boxes and prepend it
    VCN_PAD = 40
    if group_boxes:
        bx = min(b.x for b in group_boxes) - VCN_PAD
        by = min(b.y for b in group_boxes) - VCN_PAD
        bx2 = max(b.x + b.w for b in group_boxes) + VCN_PAD
        by2 = max(b.y + b.h for b in group_boxes) + VCN_PAD
        vcn_box = PositionedBox(
            id="vcn_box", label="VCN",
            box_type="_region_box",
            x=bx, y=by, w=bx2 - bx, h=by2 - by,
        )
        group_boxes = [vcn_box] + group_boxes

    return positioned, group_boxes


def spec_to_draw_dict(
    layout_spec: dict | str,
    items_by_id: dict[str, object],
) -> dict:
    """
    Convert layout spec + ServiceItems → a dict that drawio_generator.py accepts.
    Returns {"nodes": [...], "boxes": [...], "edges": [...]}.

    boxes contains PositionedBox entries (region/AD/FD/subnet boxes).
    nodes contains icon nodes only (no group boxes mixed in).

    Injects fixed edges for standard OCI connectivity patterns.
    """
    if isinstance(layout_spec, str):
        layout_spec = json.loads(layout_spec)

    is_legacy = "layers" in layout_spec and "regions" not in layout_spec

    nodes_out, boxes_out = compute_positions(layout_spec)
    edges_spec = layout_spec.get("edges", [])

    # ── Build index structures ─────────────────────────────────────────────────
    node_by_id = {n.id: n for n in nodes_out}
    box_by_id  = {b.id: b for b in boxes_out}

    # Collect all node/box IDs to check what exists
    all_ids = set(node_by_id.keys()) | set(box_by_id.keys())

    # Collect LLM-provided edge source/target pairs to avoid duplicates
    existing_pairs: set[tuple[str, str]] = set()
    for e in edges_spec:
        existing_pairs.add((e.get("source", ""), e.get("target", "")))

    draw_edges = []

    # Copy spec edges
    for e in edges_spec:
        draw_edges.append({
            "id":     e.get("id", f"e_{e.get('source','')}_{e.get('target','')}"),
            "source": e.get("source", ""),
            "target": e.get("target", ""),
            "label":  e.get("label", ""),
            "exitX":  e.get("exitX", 0.5),
            "exitY":  e.get("exitY", 1.0),
            "entryX": e.get("entryX", 0.5),
            "entryY": e.get("entryY", 0.0),
        })

    deployment_type = layout_spec.get("deployment_type", "single_ad")

    # ── Find key node/box IDs for fixed edge injection ─────────────────────────
    def _find_first(id_hints: list[str]) -> str | None:
        for hint in id_hints:
            if hint in all_ids:
                return hint
        return None

    def _find_by_oci_type(oci_type: str) -> str | None:
        for n in nodes_out:
            if n.oci_type == oci_type:
                return n.id
        return None

    def _find_subnet_by_tier(tier: str) -> str | None:
        for b in boxes_out:
            if b.box_type == "_subnet_box" and b.tier == tier:
                return b.id
        return None

    def _add_edge(src: str | None, tgt: str | None, label: str,
                  ex=0.5, ey=1.0, nx=0.5, ny=0.0):
        if src and tgt and (src, tgt) not in existing_pairs:
            if src in all_ids and tgt in all_ids:
                draw_edges.append({
                    "id":     f"e_{src}_{tgt}",
                    "source": src,
                    "target": tgt,
                    "label":  label,
                    "exitX":  ex, "exitY":  ey,
                    "entryX": nx, "entryY": ny,
                })
                existing_pairs.add((src, tgt))

    # Fixed topology edges
    drg_id    = _find_by_oci_type("drg")
    igw_id    = _find_by_oci_type("internet gateway")
    nat_id    = _find_by_oci_type("nat gateway")
    sgw_id    = _find_by_oci_type("service gateway")

    web_sub   = _find_subnet_by_tier("web")
    app_sub   = _find_subnet_by_tier("app")
    db_sub    = _find_subnet_by_tier("db")

    # Find ingress subnets — prefer explicit tier names, fall back to positional
    pub_ingress  = (_find_subnet_by_tier("public_ingress")
                    or _find_subnet_by_tier("ingress"))
    priv_ingress = (_find_subnet_by_tier("private_ingress")
                    or _find_subnet_by_tier("ingress"))
    # If only generic "ingress" subnets exist, use positional logic:
    # first subnet = private (DRG side), last = public (internet side)
    if pub_ingress == priv_ingress:
        generic_ingress = [b for b in boxes_out
                           if b.box_type == "_subnet_box" and b.tier == "ingress"]
        if len(generic_ingress) >= 2:
            priv_ingress = generic_ingress[0].id
            pub_ingress  = generic_ingress[-1].id

    oci_svc_node = None
    for region_spec in layout_spec.get("regions", []):
        svcs = region_spec.get("oci_services", [])
        if svcs:
            oci_svc_node = svcs[0]["id"]
            break

    ext_internet = next(
        (e["id"] for e in layout_spec.get("external", [])
         if e.get("type", "").lower() in {"internet", "public internet"}),
        None
    )

    # DRG → first private ingress subnet
    _add_edge(drg_id, priv_ingress, "HTTP", ex=1.0, ey=0.5, nx=0.0, ny=0.5)
    # IGW → first public ingress subnet
    _add_edge(igw_id, pub_ingress, "HTTPS/443", ex=0.5, ey=1.0, nx=0.5, ny=0.0)
    # Public LB subnet → web tier
    _add_edge(pub_ingress, web_sub, "HTTP", ex=0.5, ey=1.0, nx=0.5, ny=0.0)
    # Web → app
    _add_edge(web_sub, app_sub, "HTTP", ex=0.5, ey=1.0, nx=0.5, ny=0.0)
    # App → DB
    _add_edge(app_sub, db_sub, "Data Access", ex=0.5, ey=1.0, nx=0.5, ny=0.0)
    # NAT → internet
    _add_edge(nat_id, ext_internet, "Outbound", ex=1.0, ey=0.5, nx=0.0, ny=0.5)
    # Service GW → first OCI service
    _add_edge(sgw_id, oci_svc_node, "Internal", ex=1.0, ey=0.5, nx=0.0, ny=0.5)

    # Legacy-only: on_prem → vcn_box with deterministic edge id
    if is_legacy and "on_prem" in all_ids and "vcn_box" in all_ids:
        if ("on_prem", "vcn_box") not in existing_pairs:
            draw_edges.append({
                "id":     "e_on_prem_to_vcn",
                "source": "on_prem",
                "target": "vcn_box",
                "label":  "",
                "exitX":  1.0, "exitY":  0.5,
                "entryX": 0.0, "entryY": 0.5,
            })
            existing_pairs.add(("on_prem", "vcn_box"))

    # Convert PositionedBox/Node to serialisable dicts
    draw_nodes = [
        {
            "id":      n.id,
            "type":    n.oci_type,
            "label":   n.label,
            "x":       n.x,
            "y":       n.y,
            "w":       n.w,
            "h":       n.h,
        }
        for n in nodes_out
    ]

    draw_boxes = [
        {
            "id":       b.id,
            "label":    b.label,
            "box_type": b.box_type,
            "tier":     b.tier,
            "x":        b.x,
            "y":        b.y,
            "w":        b.w,
            "h":        b.h,
        }
        for b in boxes_out
    ]

    # Legacy-only: also emit group boxes as _group_box typed nodes, before icon nodes,
    # so drawio_generator can render them in correct z-order.
    if is_legacy:
        group_box_nodes = [
            {
                "id":    b.id,
                "type":  "_group_box",
                "label": b.label,
                "x":     b.x,
                "y":     b.y,
                "w":     b.w,
                "h":     b.h,
            }
            for b in boxes_out
        ]
        draw_nodes = group_box_nodes + draw_nodes

    return {"nodes": draw_nodes, "boxes": draw_boxes, "edges": draw_edges}

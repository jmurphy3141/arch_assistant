"""
agent/drawio_generator.py
--------------------------
Converts a pre-positioned draw dict → draw.io XML.

Accepts two input formats from layout_engine.spec_to_draw_dict:

New format (preferred):
  {
    "nodes": [{"id":..., "type":..., "label":..., "x":..., "y":..., "w":..., "h":...}, ...],
    "boxes": [{"id":..., "label":..., "box_type":..., "tier":..., "x":..., "y":..., "w":..., "h":...}, ...],
    "edges": [{"id":..., "source":..., "target":..., "label":..., ...}, ...]
  }

Old format (backward-compatible):
  {
    "nodes": [... including items with type="_group_box" ...],
    "edges": [...]
  }

Draw order (first emitted = furthest back in z-order):
  1. _region_box boxes
  2. _ad_box boxes
  3. _fd_box boxes
  4. _subnet_box boxes
  5. Icon nodes
  6. Edges

ALL cells are emitted at parent="1" (root). Flat structure.
Icons are positioned absolutely. Group boxes are drawn first (behind icons).
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

PAGE_W = 1654
PAGE_H = 1169

# Group box styles (OCI Redwood palette)
# Keyed by box_type or legacy label
GROUP_STYLES = {
    # New box_type keys
    "_region_box":     "whiteSpace=wrap;html=1;strokeWidth=2;dashed=0;rounded=1;arcSize=2;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#312D2A;strokeColor=#312D2A;fontSize=13;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_vcn_box":        "whiteSpace=wrap;html=1;strokeWidth=2;dashed=0;rounded=1;arcSize=2;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#FFFFFF;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_ad_box":         "whiteSpace=wrap;html=1;strokeWidth=1;dashed=0;rounded=1;arcSize=3;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#EAEAE8;fontColor=#312D2A;strokeColor=#312D2A;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_fd_box":         "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;rounded=1;arcSize=3;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#5F5F5C;fontSize=10;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_region_stub":    "whiteSpace=wrap;html=1;strokeWidth=2;dashed=1;rounded=1;arcSize=2;align=center;fontFamily=Oracle Sans;verticalAlign=middle;fillColor=#F5F4F2;fontColor=#9E9E9A;strokeColor=#9E9E9A;fontSize=11;fontStyle=2;",
    "_compartment_box": "whiteSpace=wrap;html=1;strokeWidth=2;dashed=0;rounded=1;arcSize=3;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#312D2A;fontSize=12;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_subnet_ingress": "whiteSpace=wrap;html=1;strokeWidth=1;dashed=0;rounded=1;arcSize=5;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#FFFFFF;fontColor=#AE562C;strokeColor=#AE562C;fontSize=10;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_subnet_web":     "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;rounded=1;arcSize=5;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#EEF3F8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=10;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_subnet_app":     "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;rounded=1;arcSize=5;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#AE562C;strokeColor=#AE562C;fontSize=10;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "_subnet_db":      "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;rounded=1;arcSize=5;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#DFDCD8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=10;fontStyle=1;spacingLeft=8;spacingTop=5;",
    # Legacy label-based keys (old format)
    "VCN":                   "whiteSpace=wrap;html=1;strokeWidth=2;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "Public Subnet":         "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#EEF3F8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "App Subnet":            "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "DB Subnet":             "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#DFDCD8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "OCI Region Services":   "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#9E9892;strokeColor=#9E9892;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "Availability Domain 1": "whiteSpace=wrap;html=1;strokeWidth=1;dashed=0;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#312D2A;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "Compartment":           "whiteSpace=wrap;html=1;strokeWidth=2;dashed=0;rounded=1;arcSize=3;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#312D2A;strokeColor=#312D2A;fontSize=13;fontStyle=1;spacingLeft=8;spacingTop=5;",
}
DEFAULT_GROUP_STYLE = "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;"

ON_PREM_STYLE = "whiteSpace=wrap;html=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#FFFFFF;rounded=1;arcSize=5;strokeColor=#312D2A;fontColor=#312D2A;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;dashed=1;"

EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
    "strokeColor=#312D2A;strokeWidth=1.5;fontFamily=Oracle Sans;fontSize=9;"
    "labelBackgroundColor=#ffffff;labelBorderColor=none;align=center;"
)

# Draw order for box types (lower index = drawn first = furthest back)
BOX_TYPE_ORDER = ["_region_box", "_region_stub", "_compartment_box", "_vcn_box", "_ad_box", "_fd_box", "_subnet_box"]


def _uid() -> str:
    return str(uuid.uuid4())


def safe(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("\n", "&#xa;"))


def _subnet_style(tier: str) -> str:
    """Return the style string for a subnet box, keyed by tier."""
    # Specific ingress sub-tiers all use the ingress visual style
    if tier in ("public_ingress", "private_ingress", "bastion"):
        return GROUP_STYLES["_subnet_ingress"]
    key = f"_subnet_{tier}" if tier else "_subnet_app"
    return GROUP_STYLES.get(key, GROUP_STYLES["_subnet_app"])


def generate_drawio(draw_dict: dict | str, output_path) -> Path:
    """
    Convert a pre-positioned draw dict → draw.io XML file.
    draw_dict comes from layout_engine.spec_to_draw_dict().

    Accepts both new format {"nodes":..., "boxes":..., "edges":...}
    and old format {"nodes":... (with _group_box items), "edges":...}.
    """
    if isinstance(draw_dict, str):
        draw_dict = json.loads(draw_dict)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    xml = _build(draw_dict)
    output_path.write_text(xml, encoding="utf-8")
    logger.info("Wrote .drawio: %s", output_path)
    return output_path


def _build(draw_dict: dict) -> str:
    from agent.oci_standards import get_icon_xml, get_icon_title, get_icon_size

    nodes = draw_dict.get("nodes", [])
    edges = draw_dict.get("edges", [])

    # ── Normalise: handle both new (separate "boxes") and old (_group_box in nodes) ──
    explicit_boxes = draw_dict.get("boxes", [])

    # Separate _group_box entries from icon nodes in old format
    legacy_group_nodes = [n for n in nodes if n.get("type") == "_group_box"]
    icon_nodes         = [n for n in nodes if n.get("type") != "_group_box"]

    # Build ID → node lookup (icon nodes only)
    node_by_id = {n["id"]: n for n in icon_nodes}

    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    # ── 1. Emit region boxes ─────────────────────────────────────────────────
    for box_type in BOX_TYPE_ORDER:
        # New format boxes
        for b in explicit_boxes:
            if b.get("box_type") != box_type:
                continue
            if box_type == "_subnet_box":
                style = _subnet_style(b.get("tier", "app"))
            else:
                style = GROUP_STYLES.get(box_type, DEFAULT_GROUP_STYLE)
            cid = b["id"]
            cells.append(
                f'<mxCell id="{cid}" value="{safe(b["label"])}" style="{style}" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{b["x"]:.0f}" y="{b["y"]:.0f}" '
                f'width="{b["w"]:.0f}" height="{b["h"]:.0f}" as="geometry"/></mxCell>'
            )

    # Legacy _group_box nodes (old format) — emit after new boxes
    for n in legacy_group_nodes:
        style = GROUP_STYLES.get(n["label"], DEFAULT_GROUP_STYLE)
        cid   = n["id"]
        cells.append(
            f'<mxCell id="{cid}" value="{safe(n["label"])}" style="{style}" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{n["x"]:.0f}" y="{n["y"]:.0f}" '
            f'width="{n["w"]:.0f}" height="{n["h"]:.0f}" as="geometry"/></mxCell>'
        )

    # ── 2. Emit on_prem box ───────────────────────────────────────────────────
    for n in icon_nodes:
        if n.get("type") == "on premises":
            cid = n["id"]
            cells.append(
                f'<mxCell id="{cid}" value="{safe(n["label"])}" style="{ON_PREM_STYLE}" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{n["x"]:.0f}" y="{n["y"]:.0f}" '
                f'width="120" height="80" as="geometry"/></mxCell>'
            )

    # ── 3. Emit icon nodes ────────────────────────────────────────────────────
    ICON_TARGET = 48

    for n in icon_nodes:
        ntype = n.get("type", "")
        if ntype == "on premises":
            continue

        nid   = n["id"]
        label = safe(n.get("label", nid))
        gx    = float(n.get("x", 0))
        gy    = float(n.get("y", 0))

        title = get_icon_title(ntype)

        if not title:
            # Fallback box for unknown types
            style = "rounded=1;whiteSpace=wrap;html=1;fillColor=#F5F4F2;strokeColor=#9E9892;fontFamily=Oracle Sans;fontSize=9;"
            cells.append(
                f'<mxCell id="{nid}" value="{label}" style="{style}" vertex="1" parent="1">'
                f'<mxGeometry x="{gx:.0f}" y="{gy:.0f}" width="{ICON_TARGET}" height="{ICON_TARGET+20}" as="geometry"/></mxCell>'
            )
            continue

        raw    = get_icon_xml(title)
        ow, oh = get_icon_size(title)
        scale  = ICON_TARGET / max(ow, oh)
        sw, sh = ow * scale, oh * scale
        ox, oy = (ICON_TARGET - sw) / 2, (ICON_TARGET - sh) / 2

        group_id = f"{nid}_g"
        cells.append(
            f'<mxCell id="{group_id}" value="{label}" '
            f'style="fillColor=none;strokeColor=none;fontFamily=Oracle Sans;fontSize=10;'
            f'verticalLabelPosition=bottom;verticalAlign=top;labelBackgroundColor=none;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{gx:.1f}" y="{gy:.1f}" width="{ICON_TARGET}" height="{ICON_TARGET}" as="geometry"/></mxCell>'
        )

        try:
            tree = ET.fromstring(raw)
        except Exception:
            continue

        for i, cell in enumerate(tree.iter("mxCell")):
            if cell.get("id") in ("0", "1"):
                continue
            geo = cell.find("mxGeometry")
            if geo is None:
                continue
            style = cell.get("style", "")
            cx = float(geo.get("x", 0)) * scale + ox
            cy = float(geo.get("y", 0)) * scale + oy
            cw = float(geo.get("width",  ow)) * scale
            ch = float(geo.get("height", oh)) * scale
            cells.append(
                f'<mxCell id="{nid}_s{i}" value="" style="{style}" '
                f'vertex="1" connectable="0" parent="{group_id}">'
                f'<mxGeometry x="{cx:.2f}" y="{cy:.2f}" width="{cw:.2f}" height="{ch:.2f}" as="geometry"/></mxCell>'
            )

    # ── 4. Emit edges ─────────────────────────────────────────────────────────
    # Build a combined ID lookup: boxes + icon nodes (for edge connection)
    all_ids_for_edges: dict[str, str] = {}  # id → resolved cell id for edge connection

    # Icon nodes connect via their _g wrapper if icon exists, else plain id
    for n in icon_nodes:
        ntype = n.get("type", "")
        nid   = n["id"]
        if ntype == "on premises":
            all_ids_for_edges[nid] = nid
        else:
            title = get_icon_title(ntype)
            all_ids_for_edges[nid] = f"{nid}_g" if title else nid

    # Boxes connect by plain id
    for b in explicit_boxes:
        all_ids_for_edges[b["id"]] = b["id"]
    for n in legacy_group_nodes:
        all_ids_for_edges[n["id"]] = n["id"]

    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")

        src_cid = all_ids_for_edges.get(src_id, src_id)
        tgt_cid = all_ids_for_edges.get(tgt_id, tgt_id)

        ex = edge.get("exitX",  0.5)
        ey = edge.get("exitY",  1.0)
        nx = edge.get("entryX", 0.5)
        ny = edge.get("entryY", 0.0)

        style = (EDGE_STYLE +
                 f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;"
                 f"entryX={nx};entryY={ny};entryDx=0;entryDy=0;")

        elabel = safe(edge.get("label", ""))
        cells.append(
            f'<mxCell id="{_uid()}" value="{elabel}" style="{style}" '
            f'edge="1" source="{src_cid}" target="{tgt_cid}" parent="1">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    xml_body = "\n  ".join(cells)
    return (
        f'<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{PAGE_W}" pageHeight="{PAGE_H}" math="0" shadow="0">\n'
        f'  <root>\n  {xml_body}\n  </root>\n</mxGraphModel>'
    )

"""
agent/drawio_generator.py
--------------------------
Converts a pre-positioned draw dict → draw.io XML.

Input format (from layout_engine.spec_to_draw_dict):
  {
    "nodes": [
      {"id": ..., "type": ..., "label": ..., "x": ..., "y": ..., "w": ..., "h": ...},
      ...
    ],
    "edges": [
      {"id": ..., "source": ..., "target": ..., "label": ..., "exit": ..., "entry": ...},
      ...
    ]
  }

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
GROUP_STYLES = {
    "VCN":                 "whiteSpace=wrap;html=1;strokeWidth=2;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=none;fontColor=#AE562C;strokeColor=#AE562C;fontSize=12;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "Public Subnet":       "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#EEF3F8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "App Subnet":          "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "DB Subnet":           "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#DFDCD8;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
    "OCI Region Services": "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#9E9892;strokeColor=#9E9892;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;",
}
DEFAULT_GROUP_STYLE = "whiteSpace=wrap;html=1;strokeWidth=1;dashed=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#F5F4F2;fontColor=#AE562C;strokeColor=#AE562C;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;"

ON_PREM_STYLE = "whiteSpace=wrap;html=1;align=left;fontFamily=Oracle Sans;verticalAlign=top;fillColor=#FFFFFF;rounded=1;arcSize=5;strokeColor=#312D2A;fontColor=#312D2A;fontSize=11;fontStyle=1;spacingLeft=8;spacingTop=5;dashed=1;"

EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;"
    "strokeColor=#312D2A;strokeWidth=1.5;fontFamily=Oracle Sans;fontSize=9;"
    "labelBackgroundColor=#ffffff;labelBorderColor=none;align=center;"
)

SIDE_XY = {
    "right":  (1.0, 0.5),
    "left":   (0.0, 0.5),
    "top":    (0.5, 0.0),
    "bottom": (0.5, 1.0),
}


def _uid() -> str:
    return str(uuid.uuid4())


def safe(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("\n", "&#xa;"))


def generate_drawio(draw_dict: dict | str, output_path) -> Path:
    """
    Convert a pre-positioned draw dict → draw.io XML file.
    draw_dict comes from layout_engine.spec_to_draw_dict().
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

    # Build ID → node lookup
    node_by_id = {n["id"]: n for n in nodes}

    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']

    # ── Emit group boxes first (drawn behind icons) ───────────────────────────
    for n in nodes:
        if n.get("type") != "_group_box":
            continue
        label = n["id"]          # use id to look up style by label
        style = GROUP_STYLES.get(n["label"], DEFAULT_GROUP_STYLE)
        cid   = n["id"]
        cells.append(
            f'<mxCell id="{cid}" value="{safe(n["label"])}" style="{style}" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{n["x"]:.0f}" y="{n["y"]:.0f}" '
            f'width="{n["w"]:.0f}" height="{n["h"]:.0f}" as="geometry"/></mxCell>'
        )

    # ── Emit on_prem box ──────────────────────────────────────────────────────
    for n in nodes:
        if n.get("type") == "on premises":
            cid = n["id"]
            cells.append(
                f'<mxCell id="{cid}" value="{safe(n["label"])}" style="{ON_PREM_STYLE}" '
                f'vertex="1" parent="1">'
                f'<mxGeometry x="{n["x"]:.0f}" y="{n["y"]:.0f}" '
                f'width="120" height="80" as="geometry"/></mxCell>'
            )

    # ── Emit icon nodes ───────────────────────────────────────────────────────
    ICON_TARGET = 48

    for n in nodes:
        ntype = n.get("type", "")
        if ntype in ("_group_box", "on premises"):
            continue

        nid   = n["id"]
        label = safe(n.get("label", nid))
        gx    = float(n.get("x", 0))
        gy    = float(n.get("y", 0))

        title = get_icon_title(ntype)

        if not title:
            # Fallback box
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
            f'style="fillColor=none;strokeColor=none;fontFamily=Oracle Sans;fontSize=9;'
            f'verticalLabelPosition=bottom;verticalAlign=top;labelBackgroundColor=none;" '
            f'vertex="1" parent="1">'
            f'<mxGeometry x="{gx:.1f}" y="{gy:.1f}" width="{ICON_TARGET}" height="{ICON_TARGET+28}" as="geometry"/></mxCell>'
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

    # ── Emit edges ────────────────────────────────────────────────────────────
    for edge in edges:
        src_id = edge.get("source", "")
        tgt_id = edge.get("target", "")

        src_node = node_by_id.get(src_id, {})
        tgt_node = node_by_id.get(tgt_id, {})
        src_type = src_node.get("type", "")
        tgt_type = tgt_node.get("type", "")

        # Group boxes and on-prem connect by their plain ID
        # Icon nodes connect via _g wrapper
        NON_ICON = {"_group_box", "on premises", None}
        src_cid = src_id if src_type in NON_ICON else (f"{src_id}_g" if get_icon_title(src_type) else src_id)
        tgt_cid = tgt_id if tgt_type in NON_ICON else (f"{tgt_id}_g" if get_icon_title(tgt_type) else tgt_id)

        # Use explicit X/Y fractions from layout engine (dynamic gateway alignment)
        ex = edge.get("exitX",  1.0)
        ey = edge.get("exitY",  0.5)
        nx = edge.get("entryX", 0.0)
        ny = edge.get("entryY", 0.5)

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

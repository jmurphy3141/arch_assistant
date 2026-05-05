from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any


def inspect_drawio_xml(xml_text: str) -> dict[str, Any]:
    """
    Parse draw.io XML and return a compact view for Archie to reason about.

    Returns:
        readable      bool   — False if the XML could not be parsed
        labels        list   — non-empty cell value strings (display labels)
        cells         list   — all mxCell dicts with id + value
        search_text   str    — space-joined labels for keyword search
        error         str    — set only when readable is False
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        return {
            "readable": False,
            "labels": [],
            "cells": [],
            "search_text": "",
            "error": str(exc),
        }

    cells: list[dict[str, Any]] = []
    labels: list[str] = []

    for cell in root.iter("mxCell"):
        value = str(cell.get("value") or "").strip()
        cells.append({"id": cell.get("id", ""), "value": value})
        if value:
            labels.append(value)

    return {
        "readable": True,
        "labels": labels,
        "cells": cells,
        "search_text": " ".join(labels),
    }

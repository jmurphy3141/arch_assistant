"""
agent/tools/export.py
----------------------
ExportHandlers: turns a stored .drawio artifact into a downloadable PNG.
Wraps agent/png_exporter.py (draw.io CLI) — produces a new artifact (the PNG)
stored next to the source .drawio so existing artifact-key conventions keep
working for it too.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from agent import png_exporter
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult


class ExportHandlers:
    def __init__(
        self, store: ObjectStoreBase, customer_id: str, customer_name: str
    ) -> None:
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def export_diagram_png(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        agents = context.get("agents", {}) if isinstance(context, dict) else {}
        diagram = dict((agents or {}).get("diagram", {}) or {})
        diagram_key = str(
            diagram.get("diagram_key", "") or diagram.get("artifact_ref", "") or ""
        ).strip()
        if not diagram_key or not self._store.head(diagram_key):
            return ToolResult(
                summary="No diagram artifact found to export.",
                status="needs_input",
                clarification="Generate a diagram first with generate_diagram, then export it to PNG.",
            )

        png_key = (
            diagram_key.rsplit(".", 1)[0] + ".png"
            if diagram_key.endswith(".drawio")
            else diagram_key + ".png"
        )
        if self._store.head(png_key):
            return ToolResult(
                summary="PNG already exists for the latest diagram.",
                status="ok",
                artifact_key=png_key,
                data={"png_key": png_key, "reused": True},
            )

        drawio_bytes = self._store.get(diagram_key)
        with tempfile.TemporaryDirectory() as tmp:
            drawio_path = Path(tmp) / "diagram.drawio"
            drawio_path.write_bytes(drawio_bytes)
            png_path = png_exporter.export_png(drawio_path)
            if png_path is None or not png_path.exists():
                return ToolResult(
                    summary="PNG export failed — draw.io CLI unavailable or rendering error.",
                    status="blocked",
                )
            png_bytes = png_path.read_bytes()

        self._store.put(png_key, png_bytes, "image/png")
        return ToolResult(
            summary="Diagram exported to PNG.",
            status="ok",
            artifact_key=png_key,
            data={"png_key": png_key},
        )

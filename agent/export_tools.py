"""Native-only deterministic export of existing engagement artifacts."""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

import openpyxl

from agent import context_store, png_exporter
from agent.persistence_objectstore import ObjectStoreBase
from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


def get_export_tool_specs(
    *, store: ObjectStoreBase, engagement_id: str, customer_name: str = ""
) -> tuple[ToolSpec, ...]:
    """Build the engagement-scoped native artifact exporter."""
    tools = NativeExportTools(store, engagement_id, customer_name)
    return (
        ToolSpec(
            name="export_artifact",
            handler=tools.export_artifact,
            description=(
                "Export or convert a produced artifact to a shareable format and "
                "return a download link — the architecture diagram to a PNG image, "
                "or a spreadsheet (e.g. the BOM) to CSV. Use when the user asks for "
                "an image/PNG of the diagram, a CSV of a sheet, or an exported/"
                "shareable copy of an artifact."
            ),
            args={
                "type": ArgSchema(
                    description="Produced artifact type, such as diagram or bom.",
                    type="string",
                    required=True,
                ),
                "format": ArgSchema(
                    description="Target export format: png or csv.",
                    type="string",
                    required=True,
                ),
            },
        ),
    )


class NativeExportTools:
    def __init__(
        self, store: ObjectStoreBase, engagement_id: str, customer_name: str = ""
    ) -> None:
        self._store = store
        self._engagement_id = str(engagement_id)
        self._customer_name = str(customer_name)

    async def export_artifact(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        artifact_type = str(args.get("type") or "").strip().lower()
        target_format = str(args.get("format") or "").strip().lower().lstrip(".")
        if not artifact_type or target_format not in {"png", "csv"}:
            return ToolResult(
                summary="Unknown artifact type or export format.",
                status="needs_input",
                clarification="Provide a produced artifact type and format png or csv.",
            )

        stored_context = context_store.read_context(
            self._store, self._engagement_id, self._customer_name
        )
        indexed = context_store.get_latest_artifact_by_type(
            stored_context, artifact_type
        )
        source_key = str(indexed.get("key") or "")
        if not source_key:
            return ToolResult(
                summary=f"No produced {artifact_type} artifact was found.",
                status="needs_input",
                clarification=f"Generate the {artifact_type} artifact first.",
            )
        try:
            source = self._store.get(source_key)
        except KeyError:
            return ToolResult(
                summary=f"The stored {artifact_type} artifact is unavailable.",
                status="needs_input",
                clarification=f"Regenerate the {artifact_type} artifact first.",
            )

        source_suffix = PurePosixPath(source_key).suffix.lower()
        if target_format == "csv" and source_suffix == ".xlsx":
            try:
                converted = _xlsx_to_csv(source)
            except Exception as exc:
                return ToolResult(
                    summary=f"CSV export failed: {exc}", status="error"
                )
            content_type = "text/csv; charset=utf-8"
        elif target_format == "png" and source_suffix == ".drawio":
            if not png_exporter.DRAWIO_CLI:
                return ToolResult(
                    summary="PNG export CLI unavailable.", status="error"
                )
            converted = _drawio_to_png(source)
            if converted is None:
                return ToolResult(summary="PNG export failed.", status="error")
            content_type = "image/png"
        else:
            return ToolResult(
                summary=(
                    f"Cannot export {source_suffix or 'this artifact'} to "
                    f"{target_format}."
                ),
                status="error",
            )

        target_key = str(PurePosixPath(source_key).with_suffix(f".{target_format}"))
        self._store.put(target_key, converted, content_type)
        download_url = f"/api/download?key={quote(target_key, safe='')}"
        return ToolResult(
            summary=f"Exported {artifact_type} to {target_format.upper()}.",
            status="ok",
            artifact_key=target_key,
            data={
                "artifact_key": target_key,
                "download_url": download_url,
                "source_artifact_key": source_key,
            },
        )


def _xlsx_to_csv(content: bytes) -> bytes:
    workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
    output = StringIO(newline="")
    try:
        writer = csv.writer(output)
        for row in workbook.worksheets[0].iter_rows(values_only=True):
            writer.writerow(["" if value is None else value for value in row])
    finally:
        workbook.close()
    return output.getvalue().encode("utf-8")


def _drawio_to_png(content: bytes) -> bytes | None:
    with TemporaryDirectory(prefix="archie-export-") as directory:
        source_path = Path(directory) / "diagram.drawio"
        target_path = Path(directory) / "diagram.png"
        source_path.write_bytes(content)
        exported = png_exporter.export_png(source_path, target_path)
        if exported is None:
            return None
        return exported.read_bytes()

"""Native-only deep reads of stored engagement files and spreadsheets."""

from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

import openpyxl

from agent import context_store, document_store, note_extractor
from agent.persistence_objectstore import ObjectStoreBase
from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


_CONTENT_LIMIT = 12_000
_DESCRIPTION = (
    "Read and return the full contents of a stored spreadsheet or document — its "
    "rows, values, and sections — to answer detailed questions about what is inside "
    "(for example 'did the BOM include Gen AI tokens?', 'what is the total in this "
    "sheet?', 'review this uploaded worksheet for X'). Use whenever the answer "
    "requires looking at what a file actually contains. Different from get_document, "
    "which only reports whether a deliverable exists and gives its link."
)


def get_file_reader_tool_specs(
    *, store: ObjectStoreBase, engagement_id: str, customer_name: str = ""
) -> tuple[ToolSpec, ...]:
    """Build the engagement-scoped native file-content reader."""
    tools = NativeFileReaderTools(store, engagement_id, customer_name)
    return (
        ToolSpec(
            name="read_file_content",
            handler=tools.read_file_content,
            description=_DESCRIPTION,
            args={
                "type": ArgSchema(
                    description="Produced artifact type, such as bom, diagram, pov, or jep.",
                    type="string",
                    required=False,
                ),
                "file": ArgSchema(
                    description="Uploaded file name or stored object key to read.",
                    type="string",
                    required=False,
                ),
            },
        ),
    )


class NativeFileReaderTools:
    """Read files only from one active engagement's artifact and upload indexes."""

    def __init__(
        self, store: ObjectStoreBase, engagement_id: str, customer_name: str = ""
    ) -> None:
        self._store = store
        self._engagement_id = str(engagement_id)
        self._customer_name = str(customer_name)

    async def read_file_content(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        artifact_type = str(args.get("type") or "").strip().lower()
        file_ref = str(
            args.get("file") or args.get("name") or args.get("key") or ""
        ).strip()
        resolved = self._resolve_file(artifact_type, file_ref)
        if resolved is None:
            requested = artifact_type or file_ref or "file"
            return ToolResult(
                summary=f"No stored {requested} could be resolved.",
                status="needs_input",
                clarification=(
                    "Provide a produced artifact type or the name/key of an uploaded file."
                ),
            )

        key, content = resolved
        try:
            if PurePosixPath(key).suffix.lower() == ".xlsx" or content.startswith(
                b"PK\x03\x04"
            ) and key.lower().endswith(".xlsx"):
                text = _spreadsheet_text(content)
            else:
                extraction = note_extractor.extract_text(content, "", key)
                if extraction.get("extraction_status") != "ok":
                    return ToolResult(
                        summary=f"Could not extract readable content from {key}.",
                        status="error",
                        data={"artifact_key": key},
                    )
                text = str(extraction.get("text") or "")
        except Exception as exc:
            return ToolResult(
                summary=f"Could not read stored file {key}: {exc}",
                status="error",
                data={"artifact_key": key},
            )

        rendered, truncated = _truncate(text)
        return ToolResult(
            summary=f"Read the contents of {key}.",
            status="ok",
            data={
                "source_key": key,
                "content": rendered,
                "truncated": truncated,
            },
        )

    def _resolve_file(self, artifact_type: str, file_ref: str) -> tuple[str, bytes] | None:
        if artifact_type:
            stored_context = context_store.read_context(
                self._store, self._engagement_id, self._customer_name
            )
            indexed = context_store.get_latest_artifact_by_type(
                stored_context, artifact_type
            )
            key = str(indexed.get("key") or "")
            if key:
                try:
                    return key, self._store.get(key)
                except KeyError:
                    return None

        if not file_ref:
            return None
        notes = document_store.list_notes(self._store, self._engagement_id)
        for note in notes:
            key = str(note.get("key") or "")
            name = str(note.get("name") or "")
            if file_ref not in {key, name}:
                continue
            for candidate in (
                key,
                f"customers/{self._engagement_id}/notes/{name}",
            ):
                try:
                    return candidate, self._store.get(candidate)
                except KeyError:
                    continue
        return None


def _spreadsheet_text(content: bytes) -> str:
    workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
    sections: list[str] = []
    try:
        for worksheet in workbook.worksheets:
            rows = [f"[Sheet: {worksheet.title}]"]
            for values in worksheet.iter_rows(values_only=True):
                cells = list(values)
                while cells and cells[-1] is None:
                    cells.pop()
                if not cells or all(value is None for value in cells):
                    continue
                rows.append(" | ".join("" if value is None else str(value) for value in cells))
            sections.append("\n".join(rows))
    finally:
        workbook.close()
    return "\n\n".join(sections)


def _truncate(content: str) -> tuple[str, bool]:
    if len(content) <= _CONTENT_LIMIT:
        return content, False
    return (
        content[:_CONTENT_LIMIT]
        + f"\n\n[Content truncated at {_CONTENT_LIMIT} characters.]",
        True,
    )

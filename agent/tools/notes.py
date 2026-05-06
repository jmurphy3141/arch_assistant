"""
agent/tools/notes.py
--------------------
In-process ToolHandler implementations for notes and document retrieval.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent import archie_memory, context_store, document_store
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult


class NotesHandlers:
    def __init__(
        self, store: ObjectStoreBase, customer_id: str, customer_name: str
    ) -> None:
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def save_notes(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        text = str(args.get("text") or "")
        if not text:
            return ToolResult(
                summary="No notes provided.",
                status="needs_input",
                clarification="Please provide note text.",
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        note_key = document_store.save_note(
            self._store,
            self._customer_id,
            f"note_{timestamp}.md",
            text.encode(),
        )
        decision_context = memory.decision_context if memory else {}
        archie_memory._record_saved_note_context(
            store=self._store,
            customer_id=self._customer_id,
            customer_name=self._customer_name,
            note_key=note_key,
            note_text=text,
            decision_context=decision_context,
        )
        return ToolResult(summary="Notes saved.", status="ok", artifact_key=note_key)

    async def get_summary(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        stored_context = context_store.read_context(
            self._store, self._customer_id, self._customer_name
        )
        summary_text = context_store.build_context_summary(stored_context)
        return ToolResult(
            summary=summary_text,
            status="ok",
            data={"summary": summary_text},
        )

    async def get_document(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        doc_type = str(args.get("type") or "")
        latest = document_store.get_latest_doc(
            self._store, self._customer_id, doc_type
        )
        key, content = _normalize_latest_doc(latest)
        if not content:
            return ToolResult(
                summary=f"No {doc_type} document found.",
                status="needs_input",
                clarification=f"No {doc_type} found. Generate one first.",
            )
        return ToolResult(
            summary=f"{doc_type} retrieved.",
            status="ok",
            artifact_key=key,
            data={"content": content},
        )


def _normalize_latest_doc(latest: Any) -> tuple[str, str]:
    if latest is None:
        return "", ""
    if isinstance(latest, tuple):
        key = latest[0] if len(latest) > 0 else ""
        content = latest[1] if len(latest) > 1 else ""
        return str(key or ""), str(content or "")
    return "", str(latest or "")

"""
agent/tools/notes.py
--------------------
In-process ToolHandler implementations for notes and document retrieval.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent import archie_memory, context_store, document_sections, document_store
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult

_FULL_SECTION_CEILING = 12000


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

    async def confirm_debrief(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        pending = context.get("pending_debrief")
        if not pending or not isinstance(pending, dict):
            return ToolResult(
                summary="No pending debrief to confirm.",
                status="ok",
                data={"confirmed": 0},
            )

        context_store.merge_archie_relationship_facts(context, pending)
        context.pop("pending_debrief", None)

        counts = {
            "stakeholders": len(pending.get("stakeholders") or []),
            "action_items": len(pending.get("action_items") or []),
            "objections": len(pending.get("objections") or []),
            "commitments": len(pending.get("commitments") or []),
        }
        total = sum(counts.values())
        summary_parts = [f"{v} {k.replace('_', ' ')}" for k, v in counts.items() if v]
        summary = f"Debrief confirmed — {', '.join(summary_parts)} saved to engagement context."
        return ToolResult(summary=summary, status="ok", data={"confirmed": total, "counts": counts})

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


    async def list_documents(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        notes = document_store.list_notes(self._store, self._customer_id)
        items: list[dict[str, Any]] = []
        for note in notes:
            name = str(note.get("name") or "")
            if not name:
                continue
            text = document_store.get_note_text(self._store, self._customer_id, name)
            sections = document_sections.parse_sections(text) if text else []
            items.append({
                "name": name,
                "section_count": len(sections),
                "top_sections": [s.title for s in sections[:5]],
            })
        summary = (
            f"{len(items)} uploaded document(s) found."
            if items
            else "No uploaded documents found."
        )
        return ToolResult(summary=summary, status="ok", data={"documents": items})

    async def get_document_section(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        note_name = str(args.get("note_name") or "")
        section_ref = str(args.get("section") or "").strip()
        if not note_name:
            return ToolResult(
                summary="No document name provided.",
                status="needs_input",
                clarification="Call list_documents first to get a valid note_name.",
            )

        text = document_store.get_note_text(self._store, self._customer_id, note_name)
        if text is None:
            return ToolResult(
                summary=f"No document named '{note_name}' found.",
                status="needs_input",
                clarification="Call list_documents to see available documents.",
            )

        sections = document_sections.parse_sections(text)

        if not section_ref:
            toc = document_sections.build_toc(sections)
            return ToolResult(
                summary=f"Table of contents for '{note_name}' ({len(sections)} section(s)).",
                status="ok",
                data={"toc": toc, "note_name": note_name},
            )

        if section_ref.lower() in ("full", "*"):
            content = text
            truncated = len(content) > _FULL_SECTION_CEILING
            if truncated:
                remaining = len(content) - _FULL_SECTION_CEILING
                content = (
                    content[:_FULL_SECTION_CEILING]
                    + f"\n...[truncated, {remaining} chars remaining — request a specific section]"
                )
            return ToolResult(
                summary=f"Full text of '{note_name}'" + (" (truncated)." if truncated else "."),
                status="ok",
                data={"content": content, "note_name": note_name, "truncated": truncated},
            )

        section = document_sections.find_section(sections, section_ref)
        if section is None:
            toc = document_sections.build_toc(sections)
            return ToolResult(
                summary=f"No section matching '{section_ref}' in '{note_name}'.",
                status="needs_input",
                clarification=(
                    f"That section wasn't found. Closest table of contents:\n{toc}"
                ),
            )

        section_text = document_sections.get_section_text(text, section)
        label = f"{section.number} {section.title}".strip() if section.number else section.title
        return ToolResult(
            summary=f"Section [{label}] retrieved from '{note_name}'.",
            status="ok",
            data={
                "content": section_text,
                "note_name": note_name,
                "section_id": section.id,
                "section_number": section.number,
                "section_title": section.title,
            },
        )


def _normalize_latest_doc(latest: Any) -> tuple[str, str]:
    if latest is None:
        return "", ""
    if isinstance(latest, tuple):
        key = latest[0] if len(latest) > 0 else ""
        content = latest[1] if len(latest) > 1 else ""
        return str(key or ""), str(content or "")
    return "", str(latest or "")

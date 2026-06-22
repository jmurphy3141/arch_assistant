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

    async def update_relationship_status(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        category = str(args.get("category") or "").strip().lower()
        match_text = str(args.get("match_text") or "").strip()
        new_status = str(args.get("status") or "").strip().lower()
        if category not in ("objections", "commitments", "action_items"):
            return ToolResult(
                summary="Invalid category.",
                status="needs_input",
                clarification="category must be one of: objections, commitments, action_items.",
            )
        if not match_text or not new_status:
            return ToolResult(
                summary="match_text and status are required.",
                status="needs_input",
                clarification="Provide match_text (substring of the concern/what/task) and status.",
            )

        stored_context = context_store.read_context(
            self._store, self._customer_id, self._customer_name
        )
        rel = context_store.get_archie_state(stored_context).get("relationship") or {}
        before = [dict(item) for item in (rel.get(category) or []) if isinstance(item, dict)]

        context_store.update_relationship_item_status(
            stored_context,
            category=category,
            match_text=match_text,
            new_status=new_status,
            note=str(args.get("note") or ""),
        )

        after = [
            item
            for item in (context_store.get_archie_state(stored_context).get("relationship") or {}).get(category) or []
            if isinstance(item, dict)
        ]
        matched = sum(
            1 for b, a in zip(before, after) if a.get("status") != b.get("status")
        )
        if matched == 0:
            return ToolResult(
                summary=f"No {category} item matched '{match_text}'.",
                status="needs_input",
                clarification="Call get_open_relationship_items to see current open items and their exact wording.",
            )

        context_store.write_context(self._store, self._customer_id, stored_context)
        return ToolResult(
            summary=f"Marked {matched} {category} item(s) as {new_status}.",
            status="ok",
            data={"matched": matched, "category": category, "status": new_status},
        )

    async def get_open_relationship_items(
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
        open_items = context_store.get_open_relationship_items(stored_context)
        total = sum(len(v) for v in open_items.values())
        summary = (
            f"{len(open_items.get('objections', []))} open objection(s), "
            f"{len(open_items.get('commitments', []))} open commitment(s), "
            f"{len(open_items.get('action_items', []))} open action item(s)."
            if total
            else "No open objections, commitments, or action items."
        )
        return ToolResult(summary=summary, status="ok", data=open_items)

    async def delete_note(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        note_name = str(args.get("note_name") or "").strip()
        if not note_name:
            return ToolResult(
                summary="note_name is required.",
                status="needs_input",
                clarification="Call list_documents to get a valid note_name.",
            )
        removed = document_store.delete_note(self._store, self._customer_id, note_name)
        if not removed:
            return ToolResult(
                summary=f"No document named '{note_name}' found.",
                status="needs_input",
                clarification="Call list_documents to see available documents.",
            )
        return ToolResult(summary=f"Deleted '{note_name}'.", status="ok", data={"deleted": note_name})

    async def rename_note(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        old_name = str(args.get("old_name") or "").strip()
        new_name = str(args.get("new_name") or "").strip()
        if not old_name or not new_name:
            return ToolResult(
                summary="old_name and new_name are required.",
                status="needs_input",
                clarification="Call list_documents to get the current note name.",
            )
        renamed = document_store.rename_note(self._store, self._customer_id, old_name, new_name)
        if not renamed:
            return ToolResult(
                summary=f"Could not rename '{old_name}' to '{new_name}'.",
                status="needs_input",
                clarification="Either the source name was not found or the target name is already in use.",
            )
        return ToolResult(
            summary=f"Renamed '{old_name}' to '{new_name}'.",
            status="ok",
            data={"old_name": old_name, "new_name": new_name},
        )

    async def search_documents(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(
                summary="A search query is required.",
                status="needs_input",
                clarification="Provide a keyword or phrase to search for.",
            )
        max_hits = max(1, min(int(args.get("max_hits") or 10), 25))
        query_lower = query.lower()

        notes = document_store.list_notes(self._store, self._customer_id)
        hits: list[dict[str, Any]] = []
        for note in notes:
            name = str(note.get("name") or "")
            if not name:
                continue
            text = document_store.get_note_text(self._store, self._customer_id, name)
            if not text or query_lower not in text.lower():
                continue
            lines = text.splitlines()
            sections = document_sections.parse_sections(text)
            for line_no, line in enumerate(lines):
                if query_lower not in line.lower():
                    continue
                section = _find_section_for_line(sections, line_no)
                label = (
                    f"{section.number} {section.title}".strip()
                    if section and section.number
                    else (section.title if section else "")
                )
                hits.append({
                    "note_name": name,
                    "section_id": section.id if section else "",
                    "section_label": label,
                    "snippet": _snippet_around(lines, line_no),
                })
                if len(hits) >= max_hits:
                    break
            if len(hits) >= max_hits:
                break

        if not hits:
            return ToolResult(
                summary=f"No matches found for '{query}' across uploaded documents.",
                status="ok",
                data={"query": query, "hits": []},
            )
        summary = f"{len(hits)} match(es) for '{query}' across {len({h['note_name'] for h in hits})} document(s)."
        return ToolResult(summary=summary, status="ok", data={"query": query, "hits": hits})


def _find_section_for_line(sections: list, line_no: int):
    for s in sections:
        if s.start_line <= line_no < s.end_line:
            return s
    return None


def _snippet_around(lines: list[str], line_no: int, *, radius: int = 1, max_len: int = 220) -> str:
    start = max(0, line_no - radius)
    end = min(len(lines), line_no + radius + 1)
    snippet = " ".join(l.strip() for l in lines[start:end] if l.strip())
    return snippet[:max_len] + ("..." if len(snippet) > max_len else "")


def _normalize_latest_doc(latest: Any) -> tuple[str, str]:
    if latest is None:
        return "", ""
    if isinstance(latest, tuple):
        key = latest[0] if len(latest) > 0 else ""
        content = latest[1] if len(latest) > 1 else ""
        return str(key or ""), str(content or "")
    return "", str(latest or "")

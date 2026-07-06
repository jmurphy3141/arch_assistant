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

        is_transcript = pending.get("source_type") == "transcript"
        context_store.merge_archie_relationship_facts(context, pending)
        if is_transcript:
            context_store.merge_archie_client_facts(
                context, pending.get("client_facts") or {}
            )
            confirmed_at = datetime.now(timezone.utc).isoformat()
            for decision in pending.get("decisions") or []:
                if not isinstance(decision, dict) or not decision.get("statement"):
                    continue
                context_store.append_decision_log(
                    context,
                    {
                        **decision,
                        "source": "confirmed_transcript_debrief",
                        "confirmed_at": confirmed_at,
                    },
                )
        context.pop("pending_debrief", None)
        if is_transcript:
            context_store.write_context(self._store, self._customer_id, context)

        counts = {
            "stakeholders": len(pending.get("stakeholders") or []),
            "action_items": len(pending.get("action_items") or []),
            "objections": len(pending.get("objections") or []),
            "commitments": len(pending.get("commitments") or []),
        }
        if is_transcript:
            counts["facts"] = len(pending.get("facts") or [])
            counts["decisions"] = len(pending.get("decisions") or [])
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
        stored_context = context
        if hasattr(self._store, "get"):
            stored_context = context_store.read_context(
                self._store, self._customer_id, self._customer_name
            )
        indexed = context_store.get_latest_artifact_by_type(stored_context, doc_type)
        if indexed.get("key"):
            return ToolResult(
                summary=str(indexed.get("summary") or f"{doc_type} retrieved."),
                status="ok",
                artifact_key=str(indexed["key"]),
                data={
                    "summary": str(indexed.get("summary") or ""),
                    "download": str(indexed.get("download") or ""),
                },
            )
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

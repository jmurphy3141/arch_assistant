"""Bounded working memory and engagement-scoped retrieval tools for native Archie."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from agent import archie_memory, context_store, document_store
from agent.engagement_mission import EngagementMission
from agent.persistence_objectstore import ObjectStoreBase
from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_DEFAULT_WORKING_SET_TURNS = 6
_DEFAULT_SUMMARY_THRESHOLD = 16
_DEFAULT_CHAR_BUDGET = 24_000


def load_memory_settings() -> dict[str, int]:
    """Load native-memory limits, using conservative defaults for old configs."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as config_file:
            orchestrator = (yaml.safe_load(config_file) or {}).get("orchestrator", {})
    except (OSError, yaml.YAMLError):
        orchestrator = {}
    return {
        "working_set_turns": max(
            1, int(orchestrator.get("working_set_turns", _DEFAULT_WORKING_SET_TURNS))
        ),
        "session_summary_threshold": max(
            2,
            int(
                orchestrator.get(
                    "session_summary_threshold", _DEFAULT_SUMMARY_THRESHOLD
                )
            ),
        ),
        "working_set_char_budget": max(
            1_000,
            int(
                orchestrator.get(
                    "working_set_char_budget", _DEFAULT_CHAR_BUDGET
                )
            ),
        ),
    }


def assemble_working_set(
    *,
    context: dict[str, Any],
    session_summary: str,
    history: list[dict[str, Any]],
    working_set_turns: int,
    char_budget: int,
) -> str:
    """Return bounded native context: digest, rolling summary, and recent turns."""
    budget = max(1, int(char_budget))
    recent = list(history or [])[-max(1, int(working_set_turns)) :]
    digest = archie_memory.refresh_engagement_digest(context)
    facts = archie_memory.current_authoritative_facts(context)
    mission = EngagementMission().get_mission(context) or {}
    sections = [
        ("[ENGAGEMENT FACT DIGEST]", digest),
        ("[AUTHORITATIVE FACTS]", json.dumps(facts, ensure_ascii=False, sort_keys=True)),
        ("[LIVE C3E PHASE STATE]", json.dumps(mission, ensure_ascii=False, sort_keys=True)),
        ("[ROLLING SESSION SUMMARY]", str(session_summary or "No older turns summarized.")),
        (
            "[RECENT SESSION TURNS]",
            _render_recent_turns(recent, max(80, int(budget * 0.20))),
        ),
    ]
    rendered = _fit_sections(sections, budget)
    for value in archie_memory.superseded_fact_values(context):
        if value:
            rendered = re.sub(re.escape(value), "[superseded]", rendered, flags=re.IGNORECASE)
    return rendered[:budget]


def get_memory_tool_specs(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    customer_name: str = "",
) -> tuple[ToolSpec, ...]:
    """Build native-only tools closed over exactly one active engagement."""
    tools = NativeMemoryTools(store, engagement_id, customer_name)
    query_arg = {
        "query": ArgSchema(
            description="Keywords for the specific fact or uploaded note to retrieve.",
            type="string",
            required=True,
        )
    }
    return (
        ToolSpec(
            name="recall_fact",
            handler=tools.recall_fact,
            description=(
                "Use this when the user asks for the current value of ONE specific "
                "engagement fact. NOT for reading a deliverable; use get_document. "
                "NOT for searching uploaded notes; use search_notes."
            ),
            args=query_arg,
        ),
        ToolSpec(
            name="search_notes",
            handler=tools.search_notes,
            description=(
                "Use this to keyword-search uploaded NOTES in the active engagement. "
                "NOT for produced artifacts; use list_artifacts or get_document. "
                "NOT for the current value of a known fact; use recall_fact."
            ),
            args=query_arg,
        ),
        ToolSpec(
            name="get_decisions",
            handler=tools.get_decisions,
            description="Return decisions recorded for the active engagement.",
        ),
        ToolSpec(
            name="list_artifacts",
            handler=tools.list_artifacts,
            description=(
                "Use this to list EVERY produced deliverable and its key or link when "
                "the user asks 'what have we produced?' or 'do we have anything?'. "
                "NOT for reading one artifact; use get_document. NOT for engagement "
                "facts; use get_summary or recall_fact."
            ),
        ),
        ToolSpec(
            name="get_meeting_summaries",
            handler=tools.get_meeting_summaries,
            description=(
                "Use this for per-meeting or per-session summaries across the engagement. "
                "NOT for the engagement's gathered fact summary; use get_summary. "
                "NOT for uploaded note search; use search_notes."
            ),
        ),
    )


class NativeMemoryTools:
    """Tool handlers whose storage scope cannot be overridden by model arguments."""

    def __init__(
        self, store: ObjectStoreBase, engagement_id: str, customer_name: str = ""
    ) -> None:
        self._store = store
        self._engagement_id = str(engagement_id)
        self._customer_name = str(customer_name)

    def _context(self) -> dict[str, Any]:
        return context_store.read_context(
            self._store, self._engagement_id, self._customer_name
        )

    async def recall_fact(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        facts = archie_memory.current_authoritative_facts(self._context())
        matches = _search_values(facts, query)
        return ToolResult(
            summary=(
                f"Found {len(matches)} current fact(s)."
                if matches
                else "No matching current fact is recorded."
            ),
            status="ok",
            data={"query": query, "facts": matches},
        )

    async def search_notes(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        query = str(args.get("query") or "").strip()
        matches = search_documents(self._store, self._engagement_id, query)
        return ToolResult(
            summary=f"Found {len(matches)} matching note(s).",
            status="ok",
            data={"query": query, "matches": matches},
        )

    async def get_decisions(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        stored = self._context()
        archie = context_store.get_archie_state(stored)
        decisions = {
            "resolved": dict(archie.get("resolved_decisions") or {}),
            "decision_log": list(stored.get("decision_log") or []),
        }
        return ToolResult(
            summary="Retrieved decisions for the active engagement.",
            status="ok",
            data=decisions,
        )

    async def list_artifacts(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        stored = self._context()
        artifacts = _artifact_references(stored)
        return ToolResult(
            summary=f"Found {len(artifacts)} artifact reference(s).",
            status="ok",
            data={"artifacts": artifacts},
        )

    async def get_meeting_summaries(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult:
        archie = context_store.get_archie_state(self._context())
        summaries = list(archie.get("session_summaries") or [])
        current = document_store.load_conversation_summary(
            self._store, self._engagement_id
        ).strip()
        if current and not any(item.get("summary") == current for item in summaries):
            summaries.append({"session_id": self._engagement_id, "summary": current})
        return ToolResult(
            summary=f"Retrieved {len(summaries)} meeting/session summary record(s).",
            status="ok",
            data={"summaries": summaries},
        )


def search_documents(
    store: ObjectStoreBase, engagement_id: str, query: str
) -> list[dict[str, str]]:
    """Keyword-search readable notes without leaving the active engagement scope."""
    terms = [term for term in re.findall(r"[a-z0-9_.-]+", query.lower()) if term]
    matches: list[dict[str, str]] = []
    readable = document_store.get_all_notes_text(store, engagement_id)
    note_text = {
        name: body
        for name, body in re.findall(
            r"^=== (.+?) ===\n(.*?)(?=^=== .+? ===\n|\Z)",
            readable,
            flags=re.MULTILINE | re.DOTALL,
        )
    }
    for note in document_store.list_notes(store, engagement_id):
        name = str(note.get("name") or "")
        text = note_text.get(name, "")
        haystack = f"{name}\n{text}".lower()
        if terms and not all(term in haystack for term in terms):
            continue
        matches.append(
            {
                "name": name,
                "key": str(note.get("key") or ""),
                "snippet": _matching_snippet(text, terms),
            }
        )
    return matches


def _fit_sections(sections: list[tuple[str, str]], budget: int) -> str:
    headers = sum(len(header) + 2 for header, _ in sections)
    available = max(0, budget - headers)
    weights = (0.22, 0.22, 0.14, 0.16, 0.26)
    chunks: list[str] = []
    for index, (header, content) in enumerate(sections):
        allowance = int(available * weights[index])
        value = str(content or "")
        if len(value) > allowance:
            value = value[: max(0, allowance - 1)] + "…"
        chunks.append(f"{header}\n{value}")
    return "\n\n".join(chunks)


def _render_recent_turns(turns: list[dict[str, Any]], budget: int) -> str:
    if not turns:
        return "[]"
    per_turn = max(12, (budget // len(turns)) - 40)
    compact = []
    for turn in turns:
        item = {
            "role": str(turn.get("role") or ""),
            "content": str(turn.get("content") or "")[:per_turn],
        }
        if turn.get("artifacts"):
            item["artifacts"] = turn.get("artifacts")
        compact.append(item)
    return json.dumps(compact, ensure_ascii=False, default=str)


def _search_values(value: Any, query: str, path: str = "") -> dict[str, Any]:
    lowered = query.lower().strip()
    broad = not lowered or any(
        phrase in lowered
        for phrase in ("everything", "all facts", "what we know", "what do we know")
    )
    terms = [] if broad else [
        term for term in re.findall(r"[a-z0-9_.-]+", lowered) if term
    ]
    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            found.update(_search_values(item, query, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_search_values(item, query, f"{path}[{index}]"))
    else:
        haystack = f"{path} {value}".lower()
        if not terms or all(term in haystack for term in terms):
            found[path or "value"] = value
    return found


def _artifact_references(context: dict[str, Any]) -> list[dict[str, str]]:
    found: dict[str, str] = {}

    def visit(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, str) and value and (
            path.endswith(("_key", ".key", "_url", ".url", "_link", ".link"))
            or "/artifacts/" in value
            or "/v" in value and "/" in value
        ):
            found[path] = value

    indexed = context.get("artifacts", {})
    has_indexed_artifacts = False
    if isinstance(indexed, dict):
        for artifact_type, entry in indexed.items():
            if not isinstance(entry, dict) or not entry.get("key"):
                continue
            has_indexed_artifacts = True
            found[str(artifact_type)] = str(entry["key"])
    if not has_indexed_artifacts:
        visit(context.get("agents", {}), "agents")
        visit(context_store.get_archie_state(context).get("work_products", {}), "work_products")
    return [{"type": path, "key": key, "link": key} for path, key in sorted(found.items())]


def _matching_snippet(text: str, terms: list[str], limit: int = 400) -> str:
    if not text:
        return ""
    lower = text.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - 80)
    return text[start : start + limit].strip()

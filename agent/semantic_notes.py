"""Engagement-isolated semantic retrieval for indexed transcript passages."""

from __future__ import annotations

import math
from typing import Any

import anyio

from agent import embedding_client, transcript_ingest
from agent.embedding_client import EmbedFn
from agent.persistence_objectstore import ObjectStoreBase
from skillforge import ArgSchema
from skillforge.registry import ToolSpec
from skillforge.types import MemorySnapshot, ToolResult


def get_semantic_tool_specs(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    embed_fn: EmbedFn | None = None,
) -> tuple[ToolSpec, ...]:
    """Build native-only semantic tools closed over one engagement namespace."""
    tools = SemanticNotesTools(store, engagement_id, embed_fn=embed_fn)
    return (
        ToolSpec(
            name="semantic_search",
            handler=tools.semantic_search,
            description=(
                "Use this to search the active engagement's transcript passages by "
                "MEANING, including paraphrases and concept matches. Use search_notes "
                "instead for exact keyword matches in ordinary uploaded notes. Never "
                "searches another engagement."
            ),
            args={
                "query": ArgSchema(
                    description="Natural-language concept or paraphrased question to retrieve.",
                    type="string",
                    required=True,
                )
            },
        ),
    )


class SemanticNotesTools:
    """Semantic handler whose index key is fixed to the active engagement."""

    def __init__(
        self,
        store: ObjectStoreBase,
        engagement_id: str,
        *,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._store = store
        self._engagement_id = str(engagement_id)
        self._embed_fn = embed_fn

    async def semantic_search(
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
                summary="A semantic query is required.",
                status="needs_input",
                clarification="What meaning or concept should I search for?",
            )
        embed_fn = self._embed_fn or embedding_client.build_embed_fn()
        matches = await anyio.to_thread.run_sync(
            lambda: semantic_search_index(
                store=self._store,
                engagement_id=self._engagement_id,
                query=query,
                embed_fn=embed_fn,
            )
        )
        return ToolResult(
            summary=(
                f"Found {len(matches)} semantically related transcript passage(s)."
                if matches
                else "No semantically related transcript passage was found."
            ),
            status="ok",
            data={"query": query, "matches": matches},
        )


def semantic_search_index(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    query: str,
    embed_fn: EmbedFn,
    top_k: int = 5,
    min_score: float = 0.05,
) -> list[dict[str, Any]]:
    """Cosine-rank only the explicitly named engagement's transcript chunks."""
    index = transcript_ingest.load_transcript_index(store, engagement_id)
    chunks = index.get("chunks") or []
    if not chunks:
        return []
    query_vectors = embed_fn([query], input_type="SEARCH_QUERY")
    if len(query_vectors) != 1 or not query_vectors[0]:
        return []
    query_vector = [float(value) for value in query_vectors[0]]
    ranked = []
    for chunk in chunks:
        vector = chunk.get("embedding") if isinstance(chunk, dict) else None
        if not isinstance(vector, list) or len(vector) != len(query_vector):
            continue
        score = _cosine(query_vector, [float(value) for value in vector])
        if score < min_score:
            continue
        citation = dict(chunk.get("citation") or {})
        passage = str(chunk.get("text") or "")
        meeting_date = str(citation.get("meeting_date") or "undated")
        meeting_id = str(citation.get("meeting_id") or chunk.get("meeting_id") or "meeting")
        line_start = citation.get("line_start", "?")
        line_end = citation.get("line_end", line_start)
        ranked.append(
            {
                "score": round(score, 6),
                "passage": passage,
                "citation": citation,
                "source_key": str(chunk.get("source_key") or ""),
                "rendered": (
                    f"per the {meeting_date} call ({meeting_id}, lines "
                    f"{line_start}-{line_end}): {passage}"
                ),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[: max(1, int(top_k))]


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)

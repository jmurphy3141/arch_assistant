"""Transcript distillation, confirmation staging, and isolated semantic indexing."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from agent import archie_memory, context_store
from agent.embedding_client import EmbedFn
from agent.persistence_objectstore import ObjectStoreBase


INDEX_SCHEMA_VERSION = "1.0"
DEFAULT_CHUNK_CHARS = 1_200
_UNCERTAIN = re.compile(r"\b(?:inaudible|unclear|uncertain|unintelligible)\b|\[\?\]", re.I)


def transcript_index_key(engagement_id: str) -> str:
    """Return the one index object owned by this engagement."""
    return f"customers/{engagement_id}/transcripts/index.json"


def ingest_transcript(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    meeting_id: str,
    transcript_text: str,
    embed_fn: EmbedFn,
    meeting_date: str = "",
    customer_name: str = "",
    source_bytes: bytes | None = None,
    source_name: str = "transcript.txt",
    content_type: str = "text/plain",
    text_runner=None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> dict[str, Any]:
    """Store/index raw transcript and stage only distilled, cited facts for confirmation."""
    text = str(transcript_text or "")
    if not text.strip():
        raise ValueError("Transcript text is required for ingestion")
    meeting = _safe_component(meeting_id or Path(source_name).stem or "meeting")
    when = str(meeting_date or date.today().isoformat())
    raw_key = store_raw_transcript(
        store=store,
        engagement_id=engagement_id,
        meeting_id=meeting,
        source_name=source_name,
        content=source_bytes if source_bytes is not None else text.encode("utf-8"),
        content_type=content_type,
    )

    chunks = chunk_transcript(text, max_chars=chunk_chars)
    vectors = embed_fn(
        [chunk["text"] for chunk in chunks], input_type="SEARCH_DOCUMENT"
    )
    if len(vectors) != len(chunks):
        raise ValueError("Embedding function must return one vector per transcript chunk")

    index = load_transcript_index(store, engagement_id)
    retained = [
        chunk
        for chunk in index.get("chunks", [])
        if str(chunk.get("meeting_id") or "") != meeting
    ]
    indexed = []
    for offset, (chunk, vector) in enumerate(zip(chunks, vectors), start=1):
        if not vector:
            raise ValueError("Embedding vectors must not be empty")
        citation = {
            "meeting_id": meeting,
            "meeting_date": when,
            "line_start": chunk["line_start"],
            "line_end": chunk["line_end"],
            "offset_start": chunk["offset_start"],
            "offset_end": chunk["offset_end"],
        }
        indexed.append(
            {
                "chunk_id": f"{meeting}:{offset}",
                "meeting_id": meeting,
                "meeting_date": when,
                "source_key": raw_key,
                "text": chunk["text"],
                "embedding": [float(value) for value in vector],
                "citation": citation,
            }
        )
    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "engagement_id": engagement_id,
        "chunks": retained + indexed,
    }
    store.put(
        transcript_index_key(engagement_id),
        json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
        "application/json",
    )

    debrief = extract_debrief(text, text_runner=text_runner)
    debrief = _cite_debrief(
        debrief,
        transcript=text,
        meeting_id=meeting,
        meeting_date=when,
    )
    staged = stage_pending_debrief(
        store=store,
        engagement_id=engagement_id,
        customer_name=customer_name,
        debrief=debrief,
    )
    return {
        "raw_key": raw_key,
        "index_key": transcript_index_key(engagement_id),
        "chunk_count": len(indexed),
        "debrief": staged,
    }


def store_raw_transcript(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    meeting_id: str,
    source_name: str,
    content: bytes,
    content_type: str = "text/plain",
) -> str:
    """Store transcript source outside the generic notes manifest."""
    meeting = _safe_component(meeting_id or Path(source_name).stem or "meeting")
    filename = _safe_filename(source_name or "transcript.txt")
    raw_key = f"customers/{engagement_id}/transcripts/raw/{meeting}/{filename}"
    store.put(raw_key, content, content_type)
    return raw_key


def extract_debrief(text: str, *, text_runner=None) -> dict[str, Any]:
    """Reuse the existing LLM extraction with its established regex fallback."""
    rel_facts: dict[str, Any] = {}
    if text_runner:
        rel_facts = archie_memory.extract_relationship_facts_llm(text, text_runner)
    if not rel_facts:
        rel_facts = archie_memory._extract_relationship_facts(text)
    client_facts = archie_memory._extract_client_facts(text)
    return {
        "stakeholders": list(rel_facts.get("stakeholders") or []),
        "action_items": list(rel_facts.get("action_items") or []),
        "objections": list(rel_facts.get("objections") or []),
        "commitments": list(rel_facts.get("commitments") or []),
        "competitive": dict(rel_facts.get("competitive") or {}),
        "client_facts": client_facts,
        "fact_count": sum(
            len(rel_facts.get(key) or [])
            for key in ("stakeholders", "action_items", "objections", "commitments")
        ),
        "pending_confirmation": True,
    }


def stage_pending_debrief(
    *,
    store: ObjectStoreBase,
    engagement_id: str,
    debrief: dict[str, Any],
    customer_name: str = "",
) -> dict[str, Any]:
    """Stage a debrief without merging any extracted fact into engagement memory."""
    context = context_store.read_context(store, engagement_id, customer_name)
    context.setdefault("pending_debrief", debrief)
    context_store.write_context(store, engagement_id, context)
    return dict(context.get("pending_debrief") or {})


def load_transcript_index(
    store: ObjectStoreBase, engagement_id: str
) -> dict[str, Any]:
    try:
        payload = json.loads(store.get(transcript_index_key(engagement_id)))
    except (KeyError, json.JSONDecodeError, TypeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict) or payload.get("engagement_id") not in (
        None,
        engagement_id,
    ):
        return {
            "schema_version": INDEX_SCHEMA_VERSION,
            "engagement_id": engagement_id,
            "chunks": [],
        }
    chunks = payload.get("chunks")
    payload["chunks"] = chunks if isinstance(chunks, list) else []
    payload["engagement_id"] = engagement_id
    payload.setdefault("schema_version", INDEX_SCHEMA_VERSION)
    return payload


def chunk_transcript(text: str, *, max_chars: int = DEFAULT_CHUNK_CHARS) -> list[dict[str, Any]]:
    """Split on lines while preserving exact source line and character offsets."""
    limit = max(80, int(max_chars))
    source_lines = str(text or "").splitlines(keepends=True)
    if not source_lines:
        return []
    chunks: list[dict[str, Any]] = []
    start_line = 1
    start_offset = 0
    buffer: list[str] = []
    buffer_len = 0
    offset = 0
    for line_number, line in enumerate(source_lines, start=1):
        if buffer and buffer_len + len(line) > limit:
            rendered = "".join(buffer).strip()
            chunks.append(
                {
                    "text": rendered,
                    "line_start": start_line,
                    "line_end": line_number - 1,
                    "offset_start": start_offset,
                    "offset_end": start_offset + buffer_len,
                }
            )
            buffer = []
            buffer_len = 0
            start_line = line_number
            start_offset = offset
        buffer.append(line)
        buffer_len += len(line)
        offset += len(line)
    if buffer:
        chunks.append(
            {
                "text": "".join(buffer).strip(),
                "line_start": start_line,
                "line_end": len(source_lines),
                "offset_start": start_offset,
                "offset_end": start_offset + buffer_len,
            }
        )
    return [chunk for chunk in chunks if chunk["text"]]


def _cite_debrief(
    debrief: dict[str, Any],
    *,
    transcript: str,
    meeting_id: str,
    meeting_date: str,
) -> dict[str, Any]:
    cited = dict(debrief)
    for key in ("stakeholders", "action_items", "objections", "commitments"):
        cited[key] = [
            _cite_item(
                item,
                transcript=transcript,
                meeting_id=meeting_id,
                meeting_date=meeting_date,
            )
            for item in cited.get(key, [])
            if isinstance(item, dict)
        ]
    if cited.get("competitive"):
        cited["competitive"] = _cite_item(
            cited["competitive"],
            transcript=transcript,
            meeting_id=meeting_id,
            meeting_date=meeting_date,
        )
    facts = []
    for field, value in _flatten(cited.get("client_facts") or {}):
        citation, source = _locate_citation(
            transcript, str(value), meeting_id, meeting_date
        )
        facts.append(
            {
                "field": field,
                "value": value,
                "citation": citation,
                "low_confidence": bool(_UNCERTAIN.search(source)),
            }
        )
    decisions = []
    for match in re.finditer(
        r"\b(?:decision|decided|agreed)(?:\s+(?:is|to|that))?[:\s]+(.{5,160}?)(?:[.\n]|$)",
        transcript,
        flags=re.IGNORECASE,
    ):
        statement = match.group(1).strip()
        citation, source = _locate_citation(
            transcript, statement, meeting_id, meeting_date
        )
        decisions.append(
            {
                "statement": statement,
                "citation": citation,
                "low_confidence": bool(_UNCERTAIN.search(source)),
            }
        )
    summary_text = _distilled_summary(cited, facts, decisions)
    whole_citation = {
        "meeting_id": meeting_id,
        "meeting_date": meeting_date,
        "line_start": 1,
        "line_end": max(1, len(transcript.splitlines())),
        "offset_start": 0,
        "offset_end": len(transcript),
    }
    cited.update(
        {
            "source_type": "transcript",
            "meeting_id": meeting_id,
            "facts": facts,
            "decisions": decisions,
            "summary": {"text": summary_text, "citation": whole_citation},
            "meetings": [
                {
                    "date": meeting_date,
                    "meeting_id": meeting_id,
                    "summary": summary_text,
                    "citation": whole_citation,
                }
            ],
            "pending_confirmation": True,
        }
    )
    cited["fact_count"] = (
        len(facts)
        + len(decisions)
        + sum(len(cited.get(key) or []) for key in ("stakeholders", "action_items", "objections", "commitments"))
    )
    return cited


def _cite_item(
    item: dict[str, Any],
    *,
    transcript: str,
    meeting_id: str,
    meeting_date: str,
) -> dict[str, Any]:
    value = max(
        (str(value) for value in item.values() if isinstance(value, (str, int, float))),
        key=len,
        default="",
    )
    citation, source = _locate_citation(transcript, value, meeting_id, meeting_date)
    return {
        **item,
        "citation": citation,
        "low_confidence": bool(_UNCERTAIN.search(source) or _UNCERTAIN.search(value)),
    }


def _locate_citation(
    transcript: str, value: str, meeting_id: str, meeting_date: str
) -> tuple[dict[str, Any], str]:
    lowered = transcript.casefold()
    needle = str(value or "").strip().casefold()
    start = lowered.find(needle) if needle else -1
    if start < 0:
        start = 0
    end = min(len(transcript), start + max(1, len(needle)))
    line_start = transcript.count("\n", 0, start) + 1
    line_end = transcript.count("\n", 0, end) + 1
    source_line_start = transcript.rfind("\n", 0, start) + 1
    source_line_end = transcript.find("\n", end)
    if source_line_end < 0:
        source_line_end = len(transcript)
    citation = {
        "meeting_id": meeting_id,
        "meeting_date": meeting_date,
        "line_start": line_start,
        "line_end": line_end,
        "offset_start": start,
        "offset_end": end,
    }
    return citation, transcript[source_line_start:source_line_end]


def _flatten(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _flatten(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten(item, f"{path}[{index}]")
    elif value not in (None, "", [], {}):
        yield path or "value", value


def _distilled_summary(
    debrief: dict[str, Any], facts: list[dict[str, Any]], decisions: list[dict[str, Any]]
) -> str:
    parts = []
    labels = (
        ("stakeholders", "stakeholder"),
        ("objections", "objection"),
        ("commitments", "commitment"),
        ("action_items", "action item"),
    )
    for key, label in labels:
        count = len(debrief.get(key) or [])
        if count:
            parts.append(f"{count} {label}{'' if count == 1 else 's'}")
    if facts:
        parts.append(f"{len(facts)} engagement fact{'s' if len(facts) != 1 else ''}")
    if decisions:
        parts.append(f"{len(decisions)} decision{'s' if len(decisions) != 1 else ''}")
    return "Meeting distilled: " + (", ".join(parts) if parts else "no structured facts extracted") + "."


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip("-._") or "meeting"


def _safe_filename(value: str) -> str:
    return _safe_component(Path(str(value or "transcript.txt")).name)

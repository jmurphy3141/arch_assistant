from __future__ import annotations

import io
import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import drawing_agent_server as srv
from agent import (
    archie_memory_retrieval,
    archie_native_loop,
    context_store,
    document_store,
    semantic_notes,
    transcript_ingest,
)
from agent.persistence_objectstore import InMemoryObjectStore
from agent.tools.notes import NotesHandlers
from skillforge.registry import ToolSpec
from skillforge.types import ToolResult


TRANSCRIPT = """Discovery call for Northwind Health on 2026-07-06.
The claims platform is subject to regulatory audits every quarter.
Jordan [unclear] is the CISO and owns the security review.
The security lead is concerned about preserving audit evidence for seven years.
We will deliver the control mapping and retention design by Friday.
Action: send the customer a retention architecture checklist.
Decision: retain quarterly audit evidence in Object Storage for seven years.
The approved deployment region is us-ashburn-1 for this engagement.
RAW_TRANSCRIPT_SENTINEL_93 must remain retrieval-only and never reach producers.
"""


def deterministic_embed(
    texts: list[str], *, input_type: str = "SEARCH_DOCUMENT"
) -> list[list[float]]:
    """Tiny test vocabulary that makes compliance a regulatory-audit paraphrase."""
    vectors = []
    for text in texts:
        tokens = re.findall(r"[a-z]+", text.casefold())
        vector = [0.0, 0.0, 0.0, 0.0]
        if "compliance" in tokens:
            vector[0] += 1.0
            vector[1] += 1.0
        vector[0] += sum(token in {"regulatory", "audit", "audits"} for token in tokens)
        vector[1] += sum(token in {"quarter", "quarterly"} for token in tokens)
        vector[2] += sum(token in {"retention", "preserving", "evidence"} for token in tokens)
        vector[3] += sum(token in {"region", "ashburn"} for token in tokens)
        vectors.append(vector)
    return vectors


def relationship_runner(_prompt: str, _system: str) -> str:
    return json.dumps(
        {
            "stakeholders": [
                {
                    "name": "Jordan",
                    "role": "CISO",
                    "disposition": "influencer",
                    "notes": "owns the security review",
                }
            ],
            "objections": [
                {
                    "concern": "preserving audit evidence for seven years",
                    "raised_by": "security lead",
                    "status": "open",
                    "response": "",
                }
            ],
            "commitments": [
                {
                    "who": "oracle",
                    "what": "deliver the control mapping and retention design by Friday",
                    "due": "",
                    "status": "open",
                }
            ],
            "action_items": [
                {
                    "owner": "se",
                    "task": "send the customer a retention architecture checklist",
                    "due": "",
                    "status": "open",
                }
            ],
        }
    )


def _ingest(store: InMemoryObjectStore, engagement_id: str = "eng-a") -> dict:
    return transcript_ingest.ingest_transcript(
        store=store,
        engagement_id=engagement_id,
        meeting_id="security-discovery",
        meeting_date="2026-07-06",
        transcript_text=TRANSCRIPT,
        embed_fn=deterministic_embed,
        source_bytes=TRANSCRIPT.encode(),
        source_name="security-discovery.txt",
        text_runner=relationship_runner,
        chunk_chars=80,
    )


@pytest.mark.asyncio
async def test_ingest_stages_cited_debrief_and_confirm_is_only_persistence_gate():
    store = InMemoryObjectStore()
    result = _ingest(store)
    pending_context = context_store.read_context(store, "eng-a", "Northwind")
    relationship = context_store.get_archie_state(pending_context)["relationship"]
    pending = pending_context["pending_debrief"]

    assert result["debrief"] == pending
    assert relationship["objections"] == []
    assert context_store.get_archie_state(pending_context)["client_facts"] == {}
    assert pending_context["decision_log"] == []
    for field in ("stakeholders", "objections", "commitments", "action_items"):
        assert pending[field]
        assert pending[field][0]["citation"]["meeting_id"] == "security-discovery"
        assert pending[field][0]["citation"]["line_start"] >= 1
    assert pending["facts"][0]["citation"]["offset_start"] >= 0
    assert pending["decisions"][0]["citation"]["meeting_id"] == "security-discovery"
    assert pending["summary"]["citation"]["line_end"] == len(TRANSCRIPT.splitlines())
    assert pending["stakeholders"][0]["low_confidence"] is True

    handler = NotesHandlers(store, "eng-a", "Northwind")
    confirmed = await handler.confirm_debrief(
        {}, memory=None, context=pending_context, trace_id="confirm"
    )
    stored = context_store.read_context(store, "eng-a", "Northwind")
    stored_archie = context_store.get_archie_state(stored)

    assert confirmed.status == "ok"
    assert "pending_debrief" not in stored
    assert stored_archie["relationship"]["objections"][0]["citation"]["meeting_id"] == "security-discovery"
    assert stored_archie["relationship"]["meetings"][0]["summary"].startswith("Meeting distilled:")
    assert stored_archie["client_facts"]["region"] == "us-ashburn-1"
    assert stored["decision_log"][0]["statement"].startswith("retain quarterly audit evidence")


def test_raw_transcript_is_chunked_embedded_and_not_a_generic_note():
    store = InMemoryObjectStore()
    result = _ingest(store)
    index = transcript_ingest.load_transcript_index(store, "eng-a")

    assert store.head(result["raw_key"])
    assert store.head("customers/eng-a/transcripts/index.json")
    assert result["chunk_count"] == 9
    assert len(index["chunks"]) == 9
    assert all(len(chunk["embedding"]) == 4 for chunk in index["chunks"])
    assert document_store.list_notes(store, "eng-a") == []
    assert document_store.get_all_notes_text(store, "eng-a") == ""


@pytest.mark.asyncio
async def test_semantic_paraphrase_recall_succeeds_when_keyword_search_misses():
    store = InMemoryObjectStore()
    _ingest(store)
    keyword_tools = archie_memory_retrieval.NativeMemoryTools(store, "eng-a")
    keyword = await keyword_tools.search_notes(
        {"query": "compliance"}, memory=None, context={}, trace_id="keyword"
    )
    semantic_tools = semantic_notes.SemanticNotesTools(
        store, "eng-a", embed_fn=deterministic_embed
    )
    semantic = await semantic_tools.semantic_search(
        {"query": "did they mention anything about compliance?"},
        memory=None,
        context={},
        trace_id="semantic",
    )

    assert keyword.data["matches"] == []
    assert semantic.data["matches"]
    assert any(
        "subject to regulatory audits every quarter" in match["passage"]
        for match in semantic.data["matches"]
    )
    assert semantic.data["matches"][0]["rendered"].startswith("per the 2026-07-06 call")


@pytest.mark.asyncio
async def test_semantic_index_is_strictly_isolated_by_engagement():
    store = InMemoryObjectStore()
    _ingest(store, "eng-a")
    tools = semantic_notes.SemanticNotesTools(
        store, "eng-b", embed_fn=deterministic_embed
    )

    result = await tools.semantic_search(
        {"query": "compliance"}, memory=None, context={}, trace_id="isolated"
    )

    assert result.data["matches"] == []
    assert not store.head("customers/eng-b/transcripts/index.json")


@pytest.mark.asyncio
async def test_native_producer_receives_only_confirmed_distillation_not_raw_transcript(monkeypatch):
    store = InMemoryObjectStore()
    _ingest(store)
    context = context_store.read_context(store, "eng-a", "Northwind")
    notes = NotesHandlers(store, "eng-a", "Northwind")
    await notes.confirm_debrief({}, memory=None, context=context, trace_id="confirm")
    captured = {}

    async def producer(args, *, context, **_kwargs):
        captured["args"] = args
        captured["context"] = json.dumps(context)
        return ToolResult(summary="Producer called.", status="ok")

    monkeypatch.setattr(
        archie_native_loop, "build_forge", lambda **_kwargs: SimpleNamespace()
    )
    monkeypatch.setattr(
        archie_native_loop,
        "get_registered_tool_specs",
        lambda _forge: (
            ToolSpec(
                name="generate_pov",
                handler=producer,
                description="Produce a POV from confirmed engagement facts.",
            ),
        ),
    )
    monkeypatch.setattr(
        archie_native_loop, "get_registered_memory", lambda _forge: SimpleNamespace()
    )
    responses = iter(({"tool": "generate_pov", "args": {}}, "Done."))

    async def tool_runner(_prompt, _system, schemas, _profile):
        assert any(schema.name == "semantic_search" for schema in schemas)
        return next(responses)

    await archie_native_loop.run_turn(
        customer_id="eng-a",
        customer_name="Northwind",
        user_message="Create the POV from confirmed facts.",
        store=store,
        text_runner=lambda *_args: "unused",
        tool_runner=tool_runner,
        embed_fn=deterministic_embed,
    )

    assert captured["args"] == {}
    assert "preserving audit evidence for seven years" in captured["context"]
    assert "RAW_TRANSCRIPT_SENTINEL_93" not in captured["context"]
    assert TRANSCRIPT not in captured["context"]


def test_upload_flag_routes_transcript_outside_generic_notes_manifest():
    store = InMemoryObjectStore()
    srv.app.state.object_store = store
    srv.app.state.text_runner = relationship_runner
    srv.app.state.embedding_runner = deterministic_embed
    srv.app.state.persistence_config = {}
    try:
        with TestClient(srv.app, raise_server_exceptions=True) as client:
            response = client.post(
                "/api/notes/upload",
                files={
                    "file": (
                        "security-discovery.txt",
                        io.BytesIO(TRANSCRIPT.encode()),
                        "text/plain",
                    )
                },
                data={
                    "customer_id": "eng-route",
                    "note_name": "security-discovery.txt",
                    "is_transcript": "true",
                },
            )
    finally:
        srv.app.state.object_store = None
        srv.app.state.text_runner = None
        srv.app.state.embedding_runner = None

    body = response.json()
    assert response.status_code == 200
    assert body["is_transcript"] is True
    assert body["transcript_chunk_count"] > 0
    assert store.head(body["transcript_index_key"])
    assert document_store.list_notes(store, "eng-route") == []


def test_semantic_tool_description_disambiguates_keyword_search():
    spec = semantic_notes.get_semantic_tool_specs(
        store=InMemoryObjectStore(),
        engagement_id="eng-a",
        embed_fn=deterministic_embed,
    )[0]

    assert spec.name == "semantic_search"
    assert "meaning" in spec.description.casefold()
    assert "paraphrase" in spec.description.casefold()
    assert "search_notes" in spec.description
    assert "keyword" in spec.description.casefold()

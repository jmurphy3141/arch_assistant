import json

import pytest

from agent import archie_memory, archie_memory_retrieval, context_store, document_store
from agent.persistence_objectstore import InMemoryObjectStore


def _context(engagement_id: str, region: str = "us-chicago-1") -> dict:
    context = context_store.read_context(
        InMemoryObjectStore(), engagement_id, engagement_id
    )
    context["customer_id"] = engagement_id
    archie_memory.merge_authoritative_facts(
        context,
        {
            "region": region,
            "platform": "VMware",
            "connectivity": {"type": "FastConnect"},
        },
    )
    return context


def test_working_set_is_bounded_and_contains_only_recent_turns():
    context = _context("eng-a")
    history = [
        {"role": "user", "content": f"turn-{index} " + ("x" * 500)}
        for index in range(100)
    ]

    result = archie_memory_retrieval.assemble_working_set(
        context=context,
        session_summary="Earlier meetings established the VMware migration scope.",
        history=history,
        working_set_turns=4,
        char_budget=3000,
    )

    assert len(result) <= 3000
    assert "ENGAGEMENT FACT DIGEST" in result
    assert "ROLLING SESSION SUMMARY" in result
    assert "VMware migration scope" in result
    assert "turn-99" in result
    assert "turn-0 " not in result
    assert "turn-95 " not in result


@pytest.mark.asyncio
async def test_fact_correction_supersedes_old_value_everywhere():
    store = InMemoryObjectStore()
    context = _context("eng-a", "us-phoenix-1")
    archie_memory.merge_authoritative_facts(context, {"region": "us-ashburn-1"})
    context_store.write_context(store, "eng-a", context)

    result = archie_memory_retrieval.assemble_working_set(
        context=context,
        session_summary="The original region was us-phoenix-1.",
        history=[
            {"role": "user", "content": "Use us-phoenix-1."},
            {"role": "user", "content": "Correction: use us-ashburn-1."},
        ],
        working_set_turns=4,
        char_budget=5000,
    )
    handler = archie_memory_retrieval.NativeMemoryTools(store, "eng-a")
    recalled = await handler.recall_fact(
        {"query": "region"}, memory=None, context={}, trace_id="t1"
    )

    assert "us-ashburn-1" in result
    assert "us-phoenix-1" not in result
    assert recalled.data["facts"] == {"facts.region": "us-ashburn-1"}
    assert "us-phoenix-1" not in json.dumps(recalled.data)


@pytest.mark.asyncio
async def test_memory_tools_are_isolated_to_active_engagement():
    store = InMemoryObjectStore()
    context_a = _context("eng-a", "us-ashburn-1")
    context_b = _context("eng-b", "eu-frankfurt-1")
    context_store.write_context(store, "eng-a", context_a)
    context_store.write_context(store, "eng-b", context_b)
    document_store.save_note(store, "eng-a", "meeting.md", b"Acme needs FastConnect.")
    document_store.save_note(store, "eng-b", "meeting.md", b"Secret merger Project Nimbus.")
    tools = archie_memory_retrieval.NativeMemoryTools(store, "eng-a")

    facts = await tools.recall_fact(
        {"query": "everything we know"}, memory=None, context=context_b, trace_id="t2"
    )
    notes = await tools.search_notes(
        {"query": "Project Nimbus"}, memory=None, context=context_b, trace_id="t3"
    )

    payload = json.dumps(facts.data)
    assert "us-ashburn-1" in payload
    assert "eu-frankfurt-1" not in payload
    assert notes.data["matches"] == []


@pytest.mark.asyncio
async def test_compaction_and_cross_session_summary_recall():
    store = InMemoryObjectStore()
    context = _context("eng-a")
    context_store.write_context(store, "eng-a", context)
    turns = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"meeting detail {index}"}
        for index in range(12)
    ]
    document_store.save_conversation_turns(store, "eng-a", turns)

    summary = archie_memory.compact_native_session(
        store=store,
        engagement_id="eng-a",
        session_id="meeting-1",
        context=context,
        threshold=8,
        retain_turns=4,
        summary_char_budget=2000,
    )
    tools = archie_memory_retrieval.NativeMemoryTools(store, "eng-a")
    recalled = await tools.get_meeting_summaries(
        {}, memory=None, context={}, trace_id="t4"
    )

    assert "meeting detail 0" in summary
    assert len(document_store.load_conversation_history(store, "eng-a", 0)) == 4
    assert recalled.data["summaries"][0]["session_id"] == "meeting-1"
    assert "meeting detail 0" in recalled.data["summaries"][0]["summary"]


def test_native_memory_specs_expose_all_retrieval_tools():
    specs = archie_memory_retrieval.get_memory_tool_specs(
        store=InMemoryObjectStore(), engagement_id="eng-a"
    )
    names = {spec.name for spec in specs}
    assert names == {
        "recall_fact",
        "search_notes",
        "get_decisions",
        "list_artifacts",
        "get_meeting_summaries",
    }


def test_native_retrieval_descriptions_state_use_and_sibling_exclusions():
    descriptions = {
        spec.name: spec.description.lower()
        for spec in archie_memory_retrieval.get_memory_tool_specs(
            store=InMemoryObjectStore(), engagement_id="eng-a"
        )
    }

    assert "one specific" in descriptions["recall_fact"]
    assert "not for" in descriptions["recall_fact"]
    assert "uploaded notes" in descriptions["search_notes"]
    assert "not for" in descriptions["search_notes"]
    assert "every produced deliverable" in descriptions["list_artifacts"]
    assert "not for" in descriptions["list_artifacts"]
    assert "per-meeting or per-session" in descriptions["get_meeting_summaries"]
    assert "not for" in descriptions["get_meeting_summaries"]

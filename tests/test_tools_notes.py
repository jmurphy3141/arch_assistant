import pytest

from agent.tools import notes as notes_module
from agent.tools.notes import NotesHandlers
from skillforge.types import MemorySnapshot


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_handlers():
    return NotesHandlers(store=object(), customer_id="cust-1", customer_name="ACME")


async def test_save_notes_ok(monkeypatch):
    def fake_save_note(store, customer_id, filename, content):
        assert customer_id == "cust-1"
        assert filename.startswith("note_")
        assert content == b"hello"
        return "notes/abc.md"

    recorded = {}

    def fake_record_saved_note_context(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(notes_module.document_store, "save_note", fake_save_note)
    monkeypatch.setattr(
        notes_module.archie_memory,
        "_record_saved_note_context",
        fake_record_saved_note_context,
    )
    snap = MemorySnapshot(
        session_id="s1", decision_context={"constraints": {"region": "us-ashburn-1"}}
    )
    notes = make_handlers()

    result = await notes.save_notes(
        {"text": "hello"}, memory=snap, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert result.artifact_key == "notes/abc.md"
    assert recorded["note_key"] == "notes/abc.md"
    assert recorded["decision_context"] == snap.decision_context


async def test_save_notes_empty_text():
    notes = make_handlers()

    result = await notes.save_notes({"text": ""}, memory=None, context={}, trace_id="t")

    assert result.status == "needs_input"


async def test_get_summary_ok(monkeypatch):
    monkeypatch.setattr(
        notes_module.context_store,
        "read_context",
        lambda store, customer_id, customer_name: {"customer_id": customer_id},
    )
    monkeypatch.setattr(
        notes_module.context_store,
        "build_context_summary",
        lambda context: "Summary text.",
    )
    notes = make_handlers()

    result = await notes.get_summary({}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert result.data["summary"] == "Summary text."


async def test_get_document_found(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "get_latest_doc",
        lambda store, doc_type, customer_id: ("docs/pov.md", "POV content"),
    )
    notes = make_handlers()

    result = await notes.get_document(
        {"type": "pov"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert result.artifact_key == "docs/pov.md"


async def test_get_document_not_found(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "get_latest_doc",
        lambda store, doc_type, customer_id: (None, None),
    )
    notes = make_handlers()

    result = await notes.get_document(
        {"type": "pov"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "needs_input"


_RFP_TEXT = (
    "1 Introduction\n"
    "This RFP covers cloud migration.\n"
    "\n"
    "4.2 Data Residency Requirements\n"
    "All customer data must remain in-region.\n"
    "\n"
    "4.3 Security Requirements\n"
    "Encryption at rest is mandatory.\n"
)


async def test_list_documents_empty(monkeypatch):
    monkeypatch.setattr(notes_module.document_store, "list_notes", lambda store, cid: [])
    notes = make_handlers()

    result = await notes.list_documents({}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert result.data["documents"] == []


async def test_list_documents_with_sections(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "list_notes",
        lambda store, cid: [{"name": "rfp.txt"}],
    )
    monkeypatch.setattr(
        notes_module.document_store,
        "get_note_text",
        lambda store, cid, name: _RFP_TEXT,
    )
    notes = make_handlers()

    result = await notes.list_documents({}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert len(result.data["documents"]) == 1
    assert result.data["documents"][0]["name"] == "rfp.txt"
    assert result.data["documents"][0]["section_count"] == 3


async def test_get_document_section_missing_note_name():
    notes = make_handlers()

    result = await notes.get_document_section(
        {}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "needs_input"


async def test_get_document_section_not_found(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store, "get_note_text", lambda store, cid, name: None
    )
    notes = make_handlers()

    result = await notes.get_document_section(
        {"note_name": "missing.txt"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "needs_input"


async def test_get_document_section_toc(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "get_note_text",
        lambda store, cid, name: _RFP_TEXT,
    )
    notes = make_handlers()

    result = await notes.get_document_section(
        {"note_name": "rfp.txt"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert "4.2" in result.data["toc"]
    assert "content" not in result.data


async def test_get_document_section_specific_section(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "get_note_text",
        lambda store, cid, name: _RFP_TEXT,
    )
    notes = make_handlers()

    result = await notes.get_document_section(
        {"note_name": "rfp.txt", "section": "4.2"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert result.data["section_number"] == "4.2"
    assert "Data Residency" in result.data["content"]
    assert "in-region" in result.data["content"]


async def test_get_document_section_unmatched(monkeypatch):
    monkeypatch.setattr(
        notes_module.document_store,
        "get_note_text",
        lambda store, cid, name: _RFP_TEXT,
    )
    notes = make_handlers()

    result = await notes.get_document_section(
        {"note_name": "rfp.txt", "section": "9.9"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "needs_input"
    assert "4.2" in result.clarification


async def test_get_document_section_full_with_truncation(monkeypatch):
    long_text = "1 Section One\n" + ("x" * 13000) + "\n"
    monkeypatch.setattr(
        notes_module.document_store,
        "get_note_text",
        lambda store, cid, name: long_text,
    )
    notes = make_handlers()

    result = await notes.get_document_section(
        {"note_name": "big.txt", "section": "full"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert result.data["truncated"] is True
    assert "truncated" in result.data["content"]

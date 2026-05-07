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

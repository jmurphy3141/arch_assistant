import pytest

from agent import context_store, document_store
from agent.tools.export import ExportHandlers
from agent.tools.notes import NotesHandlers
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_handlers(store):
    return NotesHandlers(store=store, customer_id="cust-1", customer_name="ACME")


def make_export_handlers(store):
    return ExportHandlers(store=store, customer_id="cust-1", customer_name="ACME")


def _seed_relationship(store):
    context = context_store.read_context(store, "cust-1", "ACME")
    context_store.merge_archie_relationship_facts(
        context,
        {
            "objections": [{"concern": "Cost is too high", "status": "open"}],
            "commitments": [{"who": "SE", "what": "Send pricing by Friday", "status": "open"}],
        },
    )
    context_store.write_context(store, "cust-1", context)


# ── update_relationship_status / get_open_relationship_items ─────────────────

async def test_update_relationship_status_matches():
    store = InMemoryObjectStore()
    _seed_relationship(store)
    notes = make_handlers(store)

    result = await notes.update_relationship_status(
        {"category": "objections", "match_text": "cost", "status": "addressed"},
        memory=None, context={}, trace_id="t",
    )

    assert result.status == "ok"
    assert result.data["matched"] == 1


async def test_update_relationship_status_no_match():
    store = InMemoryObjectStore()
    _seed_relationship(store)
    notes = make_handlers(store)

    result = await notes.update_relationship_status(
        {"category": "objections", "match_text": "nonexistent", "status": "addressed"},
        memory=None, context={}, trace_id="t",
    )

    assert result.status == "needs_input"


async def test_update_relationship_status_invalid_category():
    store = InMemoryObjectStore()
    notes = make_handlers(store)

    result = await notes.update_relationship_status(
        {"category": "bogus", "match_text": "x", "status": "done"},
        memory=None, context={}, trace_id="t",
    )

    assert result.status == "needs_input"


async def test_get_open_relationship_items_empty():
    store = InMemoryObjectStore()
    notes = make_handlers(store)

    result = await notes.get_open_relationship_items({}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert result.data == {"objections": [], "commitments": [], "action_items": []}


async def test_get_open_relationship_items_after_resolution():
    store = InMemoryObjectStore()
    _seed_relationship(store)
    notes = make_handlers(store)

    await notes.update_relationship_status(
        {"category": "objections", "match_text": "cost", "status": "addressed"},
        memory=None, context={}, trace_id="t",
    )
    result = await notes.get_open_relationship_items({}, memory=None, context={}, trace_id="t")

    assert result.data["objections"] == []
    assert len(result.data["commitments"]) == 1


# ── delete_note / rename_note ─────────────────────────────────────────────────

async def test_delete_note_missing_name():
    store = InMemoryObjectStore()
    notes = make_handlers(store)

    result = await notes.delete_note({}, memory=None, context={}, trace_id="t")

    assert result.status == "needs_input"


async def test_delete_note_not_found():
    store = InMemoryObjectStore()
    notes = make_handlers(store)

    result = await notes.delete_note({"note_name": "missing.md"}, memory=None, context={}, trace_id="t")

    assert result.status == "needs_input"


async def test_delete_note_ok():
    store = InMemoryObjectStore()
    document_store.save_note(store, "cust-1", "kickoff.md", b"hello")
    notes = make_handlers(store)

    result = await notes.delete_note({"note_name": "kickoff.md"}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert document_store.list_notes(store, "cust-1") == []


async def test_rename_note_ok():
    store = InMemoryObjectStore()
    document_store.save_note(store, "cust-1", "old.md", b"hello")
    notes = make_handlers(store)

    result = await notes.rename_note(
        {"old_name": "old.md", "new_name": "new.md"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    names = [n["name"] for n in document_store.list_notes(store, "cust-1")]
    assert names == ["new.md"]


async def test_rename_note_collision():
    store = InMemoryObjectStore()
    document_store.save_note(store, "cust-1", "a.md", b"a")
    document_store.save_note(store, "cust-1", "b.md", b"b")
    notes = make_handlers(store)

    result = await notes.rename_note(
        {"old_name": "a.md", "new_name": "b.md"}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "needs_input"


# ── search_documents ───────────────────────────────────────────────────────────

async def test_search_documents_requires_query():
    store = InMemoryObjectStore()
    notes = make_handlers(store)

    result = await notes.search_documents({}, memory=None, context={}, trace_id="t")

    assert result.status == "needs_input"


async def test_search_documents_no_matches_is_ok():
    store = InMemoryObjectStore()
    document_store.save_note(store, "cust-1", "rfp.txt", b"Nothing relevant here.")
    notes = make_handlers(store)

    result = await notes.search_documents({"query": "GPU"}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert result.data["hits"] == []


async def test_search_documents_finds_hit_with_section():
    store = InMemoryObjectStore()
    text = (
        "1 Introduction\n"
        "This RFP covers cloud migration.\n"
        "\n"
        "4.2 Data Residency Requirements\n"
        "All customer data must remain in-region.\n"
    )
    document_store.save_note(store, "cust-1", "rfp.txt", text.encode())
    notes = make_handlers(store)

    result = await notes.search_documents({"query": "in-region"}, memory=None, context={}, trace_id="t")

    assert result.status == "ok"
    assert len(result.data["hits"]) == 1
    hit = result.data["hits"][0]
    assert hit["note_name"] == "rfp.txt"
    assert "4.2" in hit["section_label"]
    assert "in-region" in hit["snippet"]


async def test_search_documents_respects_max_hits():
    store = InMemoryObjectStore()
    text = "\n".join(f"match line {i}" for i in range(20))
    document_store.save_note(store, "cust-1", "big.txt", text.encode())
    notes = make_handlers(store)

    result = await notes.search_documents(
        {"query": "match", "max_hits": 5}, memory=None, context={}, trace_id="t"
    )

    assert result.status == "ok"
    assert len(result.data["hits"]) == 5


# ── export_diagram_png ────────────────────────────────────────────────────────

async def test_export_diagram_png_no_diagram():
    store = InMemoryObjectStore()
    export = make_export_handlers(store)

    result = await export.export_diagram_png({}, memory=None, context={}, trace_id="t")

    assert result.status == "needs_input"


async def test_export_diagram_png_blocked_without_cli(monkeypatch):
    from agent.tools import export as export_module

    store = InMemoryObjectStore()
    store.put("customers/cust-1/diagram.drawio", b"<drawio/>", "application/xml")
    monkeypatch.setattr(export_module.png_exporter, "export_png", lambda *a, **k: None)
    export = make_export_handlers(store)
    context = {"agents": {"diagram": {"diagram_key": "customers/cust-1/diagram.drawio"}}}

    result = await export.export_diagram_png({}, memory=None, context=context, trace_id="t")

    assert result.status == "blocked"


async def test_export_diagram_png_reuses_existing(monkeypatch):
    from agent.tools import export as export_module

    store = InMemoryObjectStore()
    store.put("customers/cust-1/diagram.drawio", b"<drawio/>", "application/xml")
    store.put("customers/cust-1/diagram.png", b"fakepng", "image/png")
    called = {"count": 0}

    def fake_export_png(*a, **k):
        called["count"] += 1
        return None

    monkeypatch.setattr(export_module.png_exporter, "export_png", fake_export_png)
    export = make_export_handlers(store)
    context = {"agents": {"diagram": {"diagram_key": "customers/cust-1/diagram.drawio"}}}

    result = await export.export_diagram_png({}, memory=None, context=context, trace_id="t")

    assert result.status == "ok"
    assert result.data["reused"] is True
    assert called["count"] == 0


async def test_export_diagram_png_success(monkeypatch, tmp_path):
    from agent.tools import export as export_module

    store = InMemoryObjectStore()
    store.put("customers/cust-1/diagram.drawio", b"<drawio/>", "application/xml")

    fake_png = tmp_path / "diagram.png"
    fake_png.write_bytes(b"fakepngbytes")

    monkeypatch.setattr(export_module.png_exporter, "export_png", lambda *a, **k: fake_png)
    export = make_export_handlers(store)
    context = {"agents": {"diagram": {"diagram_key": "customers/cust-1/diagram.drawio"}}}

    result = await export.export_diagram_png({}, memory=None, context=context, trace_id="t")

    assert result.status == "ok"
    assert result.artifact_key == "customers/cust-1/diagram.png"
    assert store.get("customers/cust-1/diagram.png") == b"fakepngbytes"

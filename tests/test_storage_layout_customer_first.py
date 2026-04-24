from __future__ import annotations

import json

from agent import context_store, document_store
from agent.persistence_objectstore import InMemoryObjectStore


def test_save_note_dual_writes_customer_first_and_legacy() -> None:
    store = InMemoryObjectStore()
    key = document_store.save_note(
        store,
        "acme",
        "kickoff.md",
        b"# kickoff",
        "text/markdown",
    )
    assert key == "notes/acme/kickoff.md"
    assert store.head("notes/acme/kickoff.md")
    assert store.head("customers/acme/notes/kickoff.md")
    assert store.head("notes/acme/MANIFEST.json")
    assert store.head("customers/acme/notes/MANIFEST.json")


def test_save_doc_dual_writes_and_reads_customer_first() -> None:
    store = InMemoryObjectStore()
    result = document_store.save_doc(
        store,
        "pov",
        "acme",
        "# POV",
        {"customer_name": "ACME"},
    )
    assert result["key"] == "pov/acme/v1.md"
    assert store.head("pov/acme/v1.md")
    assert store.head("customers/acme/pov/v1.md")
    assert document_store.get_latest_doc(store, "pov", "acme") == "# POV"


def test_context_write_dual_and_read_fallback() -> None:
    store = InMemoryObjectStore()
    ctx = {"schema_version": "1.0", "customer_id": "acme", "customer_name": "ACME", "agents": {}}
    context_store.write_context(store, "acme", ctx)
    assert store.head("customers/acme/context/context.json")
    assert store.head("context/acme/context.json")

    # Backward-compat read fallback: only legacy key exists.
    store2 = InMemoryObjectStore()
    legacy_ctx = {"schema_version": "1.0", "customer_id": "beta", "customer_name": "Beta", "agents": {"pov": {}}}
    store2.put("context/beta/context.json", json.dumps(legacy_ctx).encode("utf-8"), "application/json")
    loaded = context_store.read_context(store2, "beta", "")
    assert loaded["customer_id"] == "beta"
    assert "pov" in loaded.get("agents", {})

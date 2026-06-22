from __future__ import annotations

from agent import document_store
from agent.persistence_objectstore import InMemoryObjectStore


def test_delete_note_removes_manifest_entry():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "kickoff.md", b"hello")
    assert document_store.delete_note(store, "acme", "kickoff.md") is True
    assert document_store.list_notes(store, "acme") == []
    assert document_store.get_all_notes_text(store, "acme") == ""


def test_delete_note_unknown_name_returns_false():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "kickoff.md", b"hello")
    assert document_store.delete_note(store, "acme", "missing.md") is False
    assert len(document_store.list_notes(store, "acme")) == 1


def test_rename_note_happy_path():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "old.md", b"hello")
    assert document_store.rename_note(store, "acme", "old.md", "new.md") is True
    names = [n["name"] for n in document_store.list_notes(store, "acme")]
    assert names == ["new.md"]


def test_rename_note_unknown_old_name_returns_false():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "old.md", b"hello")
    assert document_store.rename_note(store, "acme", "missing.md", "new.md") is False
    names = [n["name"] for n in document_store.list_notes(store, "acme")]
    assert names == ["old.md"]


def test_rename_note_collision_returns_false():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "a.md", b"a")
    document_store.save_note(store, "acme", "b.md", b"b")
    assert document_store.rename_note(store, "acme", "a.md", "b.md") is False
    names = sorted(n["name"] for n in document_store.list_notes(store, "acme"))
    assert names == ["a.md", "b.md"]


def test_rename_note_preserves_text_retrieval_via_raw_key():
    store = InMemoryObjectStore()
    document_store.save_note(store, "acme", "old.md", b"raw text content")
    document_store.rename_note(store, "acme", "old.md", "new.md")
    assert document_store.get_note_text(store, "acme", "new.md") == "raw text content"

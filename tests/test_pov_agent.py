"""
tests/test_pov_agent.py
------------------------
Unit tests for agent/pov_agent.py and agent/document_store.py.
All tests use InMemoryObjectStore — no OCI SDK or real LLM required.
"""
import json
import pytest

from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import (
    save_note, list_notes, get_note, get_all_notes_text,
    get_latest_doc, save_doc, list_versions,
)
from agent.pov_agent import generate_pov


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    return InMemoryObjectStore()


def fake_text_runner(prompt: str, system_message: str = "") -> str:
    """Deterministic fake text runner — returns a minimal Markdown POV."""
    return (
        "# TestCo — Oracle Cloud Point of View\n\n"
        "## Internal Visionary Press Release\n\n"
        "### Summary\nTestCo partners with Oracle.\n\n"
        "### Problem\n- Challenge 1\n\n"
        "### Solution\nOCI solves it.\n\n"
        "### Oracle Quote\n> 'Great.' — Oracle VP\n\n"
        "### Customer Quote\n> 'Excellent.' — TestCo CEO\n\n"
        "## External (Customer) Questions\n\n"
        "**Q: What challenges?**\nA: Regulatory and scale.\n\n"
        "## Internal (Oracle) Questions\n\n"
        "**Q: Tech requirements?**\nA: SOC2, GDPR.\n"
    )


# ── document_store: notes ─────────────────────────────────────────────────────

class TestDocumentStoreNotes:
    def test_save_note_returns_key(self, store):
        key = save_note(store, "cust1", "meeting1.txt", b"Hello world")
        assert key == "notes/cust1/meeting1.txt"

    def test_saved_note_is_retrievable(self, store):
        save_note(store, "cust1", "notes.md", b"# Meeting notes\n- Item 1")
        content = get_note(store, "cust1", "notes.md")
        assert "Item 1" in content

    def test_list_notes_empty(self, store):
        assert list_notes(store, "nonexistent") == []

    def test_list_notes_returns_all(self, store):
        save_note(store, "cust1", "a.txt", b"note a")
        save_note(store, "cust1", "b.txt", b"note b")
        notes = list_notes(store, "cust1")
        names = [n["name"] for n in notes]
        assert "a.txt" in names
        assert "b.txt" in names

    def test_list_notes_metadata_has_timestamp(self, store):
        save_note(store, "cust1", "note.md", b"content")
        notes = list_notes(store, "cust1")
        assert "timestamp" in notes[0]

    def test_overwrite_note_upserts_manifest(self, store):
        save_note(store, "cust1", "note.md", b"v1")
        save_note(store, "cust1", "note.md", b"v2")
        notes = list_notes(store, "cust1")
        # Should only have one entry for the same key
        keys = [n["key"] for n in notes]
        assert keys.count("notes/cust1/note.md") == 1
        # Content should be v2
        assert get_note(store, "cust1", "note.md") == "v2"

    def test_get_note_missing_returns_none(self, store):
        assert get_note(store, "cust1", "missing.txt") is None

    def test_get_all_notes_text_empty(self, store):
        assert get_all_notes_text(store, "cust1") == ""

    def test_get_all_notes_text_concatenates(self, store):
        save_note(store, "cust1", "a.txt", b"First note")
        save_note(store, "cust1", "b.txt", b"Second note")
        text = get_all_notes_text(store, "cust1")
        assert "First note" in text
        assert "Second note" in text

    def test_get_all_notes_includes_header(self, store):
        save_note(store, "cust1", "meeting.md", b"content")
        text = get_all_notes_text(store, "cust1")
        assert "meeting.md" in text

    def test_notes_are_customer_scoped(self, store):
        save_note(store, "custA", "note.txt", b"Customer A note")
        save_note(store, "custB", "note.txt", b"Customer B note")
        assert list_notes(store, "custA") != []
        assert list_notes(store, "custB") != []
        assert get_note(store, "custA", "note.txt") == "Customer A note"
        assert get_note(store, "custB", "note.txt") == "Customer B note"


# ── document_store: versioned docs ───────────────────────────────────────────

class TestDocumentStoreVersions:
    def test_save_doc_first_version(self, store):
        result = save_doc(store, "pov", "cust1", "# POV v1")
        assert result["version"] == 1
        assert "pov/cust1/v1.md" in result["key"]

    def test_save_doc_increments_version(self, store):
        save_doc(store, "pov", "cust1", "# POV v1")
        result = save_doc(store, "pov", "cust1", "# POV v2")
        assert result["version"] == 2
        assert "v2.md" in result["key"]

    def test_save_doc_writes_latest(self, store):
        save_doc(store, "pov", "cust1", "# POV content")
        latest_key = "pov/cust1/LATEST.md"
        assert store.head(latest_key)
        assert store.get(latest_key).decode() == "# POV content"

    def test_save_doc_latest_is_newest(self, store):
        save_doc(store, "pov", "cust1", "# POV v1")
        save_doc(store, "pov", "cust1", "# POV v2")
        latest = store.get("pov/cust1/LATEST.md").decode()
        assert "v2" in latest

    def test_save_doc_manifest_updated(self, store):
        save_doc(store, "pov", "cust1", "# v1")
        save_doc(store, "pov", "cust1", "# v2")
        manifest = json.loads(store.get("pov/cust1/MANIFEST.json"))
        assert len(manifest["versions"]) == 2

    def test_get_latest_doc_none_if_missing(self, store):
        assert get_latest_doc(store, "pov", "missing") is None

    def test_get_latest_doc_returns_content(self, store):
        save_doc(store, "pov", "cust1", "# The latest content")
        assert get_latest_doc(store, "pov", "cust1") == "# The latest content"

    def test_list_versions_empty(self, store):
        assert list_versions(store, "pov", "cust1") == []

    def test_list_versions_returns_all(self, store):
        save_doc(store, "pov", "cust1", "# v1")
        save_doc(store, "pov", "cust1", "# v2")
        versions = list_versions(store, "pov", "cust1")
        assert len(versions) == 2
        assert versions[0]["version"] == 1
        assert versions[1]["version"] == 2

    def test_list_versions_includes_metadata(self, store):
        save_doc(store, "pov", "cust1", "# v1", {"customer_name": "Acme"})
        versions = list_versions(store, "pov", "cust1")
        assert versions[0]["metadata"]["customer_name"] == "Acme"

    def test_versioned_copies_preserved(self, store):
        save_doc(store, "pov", "cust1", "# v1 content")
        save_doc(store, "pov", "cust1", "# v2 content")
        v1 = store.get("pov/cust1/v1.md").decode()
        v2 = store.get("pov/cust1/v2.md").decode()
        assert "v1 content" in v1
        assert "v2 content" in v2

    def test_doc_types_are_isolated(self, store):
        save_doc(store, "pov", "cust1", "# POV")
        save_doc(store, "jep", "cust1", "# JEP")
        assert get_latest_doc(store, "pov", "cust1") == "# POV"
        assert get_latest_doc(store, "jep", "cust1") == "# JEP"


# ── pov_agent: generate_pov ──────────────────────────────────────────────────

class TestGeneratePov:
    def test_generate_pov_returns_result_dict(self, store):
        result = generate_pov("cust1", "TestCo", store, fake_text_runner)
        assert result["version"] == 1
        assert "key" in result
        assert "latest_key" in result
        assert "content" in result

    def test_generate_pov_content_from_runner(self, store):
        result = generate_pov("cust1", "TestCo", store, fake_text_runner)
        assert "Oracle Cloud Point of View" in result["content"]

    def test_generate_pov_persists_to_store(self, store):
        generate_pov("cust1", "TestCo", store, fake_text_runner)
        assert store.head("pov/cust1/LATEST.md")
        assert store.head("pov/cust1/v1.md")
        assert store.head("pov/cust1/MANIFEST.json")

    def test_generate_pov_increments_version(self, store):
        generate_pov("cust1", "TestCo", store, fake_text_runner)
        result2 = generate_pov("cust1", "TestCo", store, fake_text_runner)
        assert result2["version"] == 2

    def test_generate_pov_uses_notes(self, store):
        save_note(store, "cust1", "meeting.txt", b"Customer uses GPU clusters")
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_pov("cust1", "TestCo", store, capturing_runner)
        assert any("GPU clusters" in p for p in received_prompts)

    def test_generate_pov_includes_previous_version(self, store):
        generate_pov("cust1", "TestCo", store, fake_text_runner)
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_pov("cust1", "TestCo", store, capturing_runner)
        assert any("Previous POV version" in p for p in received_prompts)

    def test_generate_pov_no_notes_still_works(self, store):
        # No notes — should still generate a skeleton POV
        result = generate_pov("cust1", "EmptyCustomer", store, fake_text_runner)
        assert result["version"] == 1
        assert result["content"]

    def test_generate_pov_customer_name_in_prompt(self, store):
        received_prompts = []

        def capturing_runner(prompt, system_message=""):
            received_prompts.append(prompt)
            return fake_text_runner(prompt, system_message)

        generate_pov("cust1", "Jane Street Capital", store, capturing_runner)
        assert any("Jane Street Capital" in p for p in received_prompts)

    def test_generate_pov_passes_system_message(self, store):
        received_system_msgs = []

        def capturing_runner(prompt, system_message=""):
            received_system_msgs.append(system_message)
            return fake_text_runner(prompt, system_message)

        generate_pov("cust1", "TestCo", store, capturing_runner)
        assert any("POV" in sm or "Point of View" in sm for sm in received_system_msgs)

    def test_generate_pov_metadata_stored(self, store):
        generate_pov("cust1", "Acme Corp", store, fake_text_runner)
        versions = list_versions(store, "pov", "cust1")
        assert versions[0]["metadata"]["customer_name"] == "Acme Corp"

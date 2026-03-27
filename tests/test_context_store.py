"""
tests/test_context_store.py
----------------------------
Unit tests for agent/context_store.py.
All tests use InMemoryObjectStore — no OCI SDK or real LLM required.
"""
import json
import pytest

from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import save_note
from agent.context_store import (
    CONTEXT_SCHEMA_VERSION,
    read_context,
    write_context,
    get_new_notes,
    record_agent_run,
    build_context_summary,
)


@pytest.fixture
def store():
    return InMemoryObjectStore()


# ── read_context / write_context ──────────────────────────────────────────────

class TestReadWriteContext:
    def test_read_context_missing_returns_empty(self, store):
        ctx = read_context(store, "new_cust")
        assert ctx["customer_id"] == "new_cust"
        assert ctx["agents"] == {}
        assert ctx["schema_version"] == CONTEXT_SCHEMA_VERSION

    def test_read_context_populates_customer_name(self, store):
        ctx = read_context(store, "cust1", "Acme Corp")
        assert ctx["customer_name"] == "Acme Corp"

    def test_write_then_read_roundtrip(self, store):
        ctx = read_context(store, "cust1", "TestCo")
        ctx["agents"]["pov"] = {"version": 1, "key": "pov/cust1/v1.md"}
        write_context(store, "cust1", ctx)

        loaded = read_context(store, "cust1")
        assert loaded["agents"]["pov"]["version"] == 1
        assert loaded["customer_id"] == "cust1"

    def test_write_context_sets_last_updated(self, store):
        ctx = read_context(store, "cust1")
        write_context(store, "cust1", ctx)
        loaded = read_context(store, "cust1")
        assert loaded["last_updated"] != ""

    def test_write_context_stored_at_expected_key(self, store):
        ctx = read_context(store, "cust1")
        write_context(store, "cust1", ctx)
        assert store.head("context/cust1/context.json")

    def test_context_is_valid_json(self, store):
        ctx = read_context(store, "cust1", "TestCo")
        write_context(store, "cust1", ctx)
        raw = store.get("context/cust1/context.json")
        parsed = json.loads(raw.decode("utf-8"))
        assert parsed["customer_id"] == "cust1"

    def test_contexts_are_customer_scoped(self, store):
        ctx_a = read_context(store, "custA", "Customer A")
        write_context(store, "custA", ctx_a)
        ctx_b = read_context(store, "custB", "Customer B")
        write_context(store, "custB", ctx_b)

        loaded_a = read_context(store, "custA")
        loaded_b = read_context(store, "custB")
        assert loaded_a["customer_name"] == "Customer A"
        assert loaded_b["customer_name"] == "Customer B"


# ── get_new_notes ─────────────────────────────────────────────────────────────

class TestGetNewNotes:
    def test_no_notes_returns_empty(self, store):
        ctx = read_context(store, "cust1")
        keys, text = get_new_notes(store, ctx, "pov")
        assert keys == []
        assert text == ""

    def test_new_note_is_returned(self, store):
        save_note(store, "cust1", "meeting1.txt", b"GPU cluster notes")
        ctx = read_context(store, "cust1")
        keys, text = get_new_notes(store, ctx, "pov")
        assert len(keys) == 1
        assert "GPU cluster notes" in text

    def test_already_seen_note_is_excluded(self, store):
        save_note(store, "cust1", "meeting1.txt", b"GPU notes")
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/meeting1.txt"], {})
        keys, text = get_new_notes(store, ctx, "pov")
        assert keys == []
        assert text == ""

    def test_agents_are_independent(self, store):
        """POV seeing a note does not prevent JEP from seeing it."""
        save_note(store, "cust1", "note.txt", b"shared note")
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/note.txt"], {})

        # JEP should still see the note
        keys, text = get_new_notes(store, ctx, "jep")
        assert len(keys) == 1
        assert "shared note" in text

    def test_new_note_after_run_is_returned(self, store):
        save_note(store, "cust1", "old.txt", b"old note")
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/old.txt"], {})

        save_note(store, "cust1", "new.txt", b"new note")
        keys, text = get_new_notes(store, ctx, "pov")
        assert len(keys) == 1
        assert "notes/cust1/new.txt" in keys
        assert "new note" in text

    def test_note_header_in_text(self, store):
        save_note(store, "cust1", "meeting.md", b"content here")
        ctx = read_context(store, "cust1")
        _, text = get_new_notes(store, ctx, "pov")
        assert "meeting.md" in text

    def test_multiple_new_notes_concatenated(self, store):
        save_note(store, "cust1", "a.txt", b"note A")
        save_note(store, "cust1", "b.txt", b"note B")
        ctx = read_context(store, "cust1")
        keys, text = get_new_notes(store, ctx, "pov")
        assert len(keys) == 2
        assert "note A" in text
        assert "note B" in text


# ── record_agent_run ──────────────────────────────────────────────────────────

class TestRecordAgentRun:
    def test_record_creates_agent_section(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/cust1/v1.md"})
        assert "pov" in ctx["agents"]

    def test_record_stores_agent_data(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"version": 2, "key": "pov/cust1/v2.md"})
        assert ctx["agents"]["pov"]["version"] == 2
        assert ctx["agents"]["pov"]["key"] == "pov/cust1/v2.md"

    def test_record_merges_note_keys(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/a.txt"], {})
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/b.txt"], {})
        incorporated = ctx["agents"]["pov"]["notes_incorporated"]
        assert "notes/cust1/a.txt" in incorporated
        assert "notes/cust1/b.txt" in incorporated

    def test_record_notes_are_sorted(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/z.txt", "notes/cust1/a.txt"], {})
        notes = ctx["agents"]["pov"]["notes_incorporated"]
        assert notes == sorted(notes)

    def test_record_sets_last_run(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {})
        assert ctx["agents"]["pov"]["last_run"] != ""

    def test_record_multiple_agents_independent(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", ["notes/cust1/a.txt"], {"version": 1})
        ctx = record_agent_run(ctx, "jep", ["notes/cust1/a.txt"], {"version": 1})
        assert ctx["agents"]["pov"]["notes_incorporated"] == ["notes/cust1/a.txt"]
        assert ctx["agents"]["jep"]["notes_incorporated"] == ["notes/cust1/a.txt"]
        assert ctx["agents"]["pov"].get("version") == 1
        assert ctx["agents"]["jep"].get("version") == 1

    def test_record_merges_with_existing_data(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"summary": "First run"})
        ctx = record_agent_run(ctx, "pov", [], {"version": 2})
        # Both summary and version should be present
        assert ctx["agents"]["pov"]["summary"] == "First run"
        assert ctx["agents"]["pov"]["version"] == 2


# ── build_context_summary ─────────────────────────────────────────────────────

class TestBuildContextSummary:
    def test_empty_context_returns_empty_string(self, store):
        ctx = read_context(store, "cust1")
        assert build_context_summary(ctx) == ""

    def test_pov_agent_appears_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/cust1/v1.md"})
        summary = build_context_summary(ctx)
        assert "POV" in summary
        assert "v1" in summary

    def test_jep_agent_appears_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "jep", [], {
            "version": 1, "key": "jep/cust1/v1.md",
            "duration_days": 14, "bom_source": "stub",
        })
        summary = build_context_summary(ctx)
        assert "JEP" in summary
        assert "14" in summary

    def test_diagram_agent_appears_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "diagram", [], {
            "version": 1, "diagram_key": "agent3/cust1/poc/v1/spec.json",
            "node_count": 8, "diagram_name": "poc_arch",
        })
        summary = build_context_summary(ctx)
        assert "Architecture Diagram" in summary or "Diagram" in summary

    def test_terraform_agent_appears_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "terraform", [], {
            "version": 1, "prefix_key": "terraform/cust1/v1", "file_count": 4,
        })
        summary = build_context_summary(ctx)
        assert "Terraform" in summary
        assert "4" in summary

    def test_waf_agent_appears_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "waf", [], {
            "version": 1, "key": "waf/cust1/v1.md", "overall_rating": "⚠️",
        })
        summary = build_context_summary(ctx)
        assert "WAF" in summary

    def test_summary_prefix_line(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/k"})
        summary = build_context_summary(ctx)
        assert summary.startswith("Prior agent outputs")

    def test_multiple_agents_all_in_summary(self, store):
        ctx = read_context(store, "cust1")
        ctx = record_agent_run(ctx, "pov", [], {"version": 1, "key": "pov/k"})
        ctx = record_agent_run(ctx, "jep", [], {
            "version": 1, "key": "jep/k", "duration_days": 14, "bom_source": "stub"
        })
        summary = build_context_summary(ctx)
        assert "POV" in summary
        assert "JEP" in summary


# ── Full flow: notes → pov run → jep run ──────────────────────────────────────

class TestContextFlowIntegration:
    def test_pov_run_updates_context(self, store):
        """After POV run, context should record pov agent data."""
        from agent.pov_agent import generate_pov

        def fake_runner(prompt, system_message=""):
            return "# TestCo POV\n## Summary\nOCI delivers value.\n"

        save_note(store, "cust1", "note.txt", b"Customer needs GPU compute.")
        generate_pov("cust1", "TestCo", store, fake_runner)

        ctx = read_context(store, "cust1")
        assert "pov" in ctx["agents"]
        assert ctx["agents"]["pov"]["version"] == 1
        assert "notes/cust1/note.txt" in ctx["agents"]["pov"]["notes_incorporated"]

    def test_pov_does_not_reingest_seen_notes(self, store):
        """Second POV run should not see notes from first run."""
        from agent.pov_agent import generate_pov

        prompts = []
        def capturing_runner(prompt, system_message=""):
            prompts.append(prompt)
            return "# POV\nContent.\n"

        save_note(store, "cust1", "note.txt", b"First note content")
        generate_pov("cust1", "TestCo", store, capturing_runner)

        # Clear prompts, run again — no new notes
        prompts.clear()
        generate_pov("cust1", "TestCo", store, capturing_runner)

        # Second run should say no new notes
        assert any("No new notes" in p for p in prompts)

    def test_jep_run_updates_context(self, store):
        """After JEP run, context should record jep agent data."""
        from agent.jep_agent import generate_jep

        bom_json = json.dumps({
            "source": "stub", "agent": "test", "note": "test",
            "duration_days": 14, "funding": "Oracle",
            "hardware": [], "software": [], "storage": [],
        })

        def fake_runner(prompt, system_message=""):
            if '"hardware"' in prompt:
                return bom_json
            return "# JEP\n## Overview\nPOC plan.\n"

        save_note(store, "cust1", "note.txt", b"GPU POC notes")
        generate_jep("cust1", "TestCo", store, fake_runner)

        ctx = read_context(store, "cust1")
        assert "jep" in ctx["agents"]
        assert ctx["agents"]["jep"]["version"] == 1

    def test_context_summary_injected_in_second_pov(self, store):
        """Second agent run should see prior agent outputs in context summary."""
        from agent.pov_agent import generate_pov
        from agent.jep_agent import generate_jep

        bom_json = json.dumps({
            "source": "stub", "agent": "test", "note": "test",
            "duration_days": 14, "funding": "Oracle",
            "hardware": [], "software": [], "storage": [],
        })

        def pov_runner(prompt, system_message=""):
            return "# POV\nOCI delivers.\n"

        def jep_runner(prompt, system_message=""):
            if '"hardware"' in prompt:
                return bom_json
            return "# JEP\nPOC plan.\n"

        save_note(store, "cust1", "note.txt", b"Notes")

        # Run POV first
        generate_pov("cust1", "TestCo", store, pov_runner)

        # Run JEP second — its prompt should include the POV context summary
        jep_prompts = []
        def jep_capturing(prompt, system_message=""):
            jep_prompts.append(prompt)
            return jep_runner(prompt, system_message)

        generate_jep("cust1", "TestCo", store, jep_capturing)
        assert any("POV" in p or "Prior agent" in p for p in jep_prompts)

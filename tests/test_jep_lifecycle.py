from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import drawing_agent_server as srv
from agent import document_store
from agent import jep_lifecycle
from agent.persistence_objectstore import InMemoryObjectStore
import agent.orchestrator_agent as orchestrator_agent


def test_sync_jep_state_transitions() -> None:
    store = InMemoryObjectStore()
    cid = "acme"

    s = jep_lifecycle.sync_jep_state(store, cid)
    assert s["state"] == "not_started"

    document_store.save_note(store, cid, "note.md", b"kickoff notes")
    s = jep_lifecycle.sync_jep_state(store, cid)
    assert s["state"] == "kickoff_ready"

    document_store.save_jep_questions(
        store,
        cid,
        [{"id": "duration", "question": "Duration?", "known_value": None}],
    )
    s = jep_lifecycle.sync_jep_state(store, cid)
    assert s["state"] == "questions_pending"

    document_store.save_jep_questions(
        store,
        cid,
        [{"id": "duration", "question": "Duration?", "known_value": None}],
        {"duration": "14 days"},
    )
    s = jep_lifecycle.sync_jep_state(store, cid)
    assert s["state"] == "ready_to_generate"

    document_store.save_doc(
        store,
        "jep",
        cid,
        "# JEP\n\nDuration: 14 days\n\nIn Scope\n- API\n\nOut of Scope\n- migration\n\nSuccess Criteria\n- pass\n\nOwners\n- SA\n\nMilestones\n- Week 1",
    )
    s = jep_lifecycle.mark_generated(store, cid)
    assert s["state"] == "generated"

    document_store.save_approved_doc(store, "jep", cid, "# Approved")
    s = jep_lifecycle.mark_approved(store, cid)
    assert s["state"] == "approved"
    assert s["is_locked"] is True

    s = jep_lifecycle.request_revision(store, cid, "Need updates")
    assert s["state"] == "revision_requested"
    assert s["is_locked"] is False


def test_extract_missing_fields_with_tbd_override() -> None:
    missing = jep_lifecycle.extract_missing_fields(
        "# JEP\n\nDuration: [TBD]\n\nSuccess Criteria: [TBD]",
        {"duration": "21 days", "success_criteria": "Latency below 15ms"},
    )
    assert "duration" not in missing
    assert "success_criteria" not in missing


def test_jep_api_lock_and_revision_flow(monkeypatch) -> None:
    store = InMemoryObjectStore()
    srv.app.state.object_store = store
    srv.app.state.persistence_config = {}

    def _fake_generate_jep(customer_id, _customer_name, store, _text_runner, **_kwargs):
        content = (
            "# JEP\n\nDuration: 14 days\n\nIn Scope\n- API\n\nOut of Scope\n- migration\n"
            "\nSuccess Criteria\n- SLO met\n\nOwners\n- SA\n\nMilestones\n- Week 1"
        )
        saved = document_store.save_doc(store, "jep", customer_id, content, {})
        saved["content"] = content
        saved["bom"] = {}
        return saved

    monkeypatch.setattr(srv, "generate_jep", _fake_generate_jep)

    with TestClient(srv.app, raise_server_exceptions=True) as client:
        gen1 = client.post(
            "/api/jep/generate",
            json={"customer_id": "acme", "customer_name": "ACME"},
        )
        assert gen1.status_code == 200
        assert gen1.json()["jep_state"]["state"] == "generated"

        approve = client.post(
            "/api/jep/approve",
            json={"customer_id": "acme", "customer_name": "ACME", "content": "# Approved JEP"},
        )
        assert approve.status_code == 200
        assert approve.json()["jep_state"]["state"] == "approved"

        blocked = client.post(
            "/api/jep/generate",
            json={"customer_id": "acme", "customer_name": "ACME"},
        )
        assert blocked.status_code == 409
        body = blocked.json()
        assert body["status"] == "policy_block"
        assert "JEP_APPROVED_LOCKED" in body["reason_codes"]

        rr = client.post(
            "/api/jep/revision-request",
            json={"customer_id": "acme", "reason": "Need expansion"},
        )
        assert rr.status_code == 200
        assert rr.json()["jep_state"]["state"] == "revision_requested"

        gen2 = client.post(
            "/api/jep/generate",
            json={"customer_id": "acme", "customer_name": "ACME"},
        )
        assert gen2.status_code == 200
        assert gen2.json()["jep_state"]["state"] == "generated"

    srv.app.state.object_store = None


def test_orchestrator_generate_jep_policy_block_trace() -> None:
    store = InMemoryObjectStore()
    document_store.save_approved_doc(store, "jep", "acme", "# Approved JEP")
    jep_lifecycle.mark_approved(store, "acme")

    summary, key, result_data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_jep",
            {},
            customer_id="acme",
            customer_name="ACME",
            store=store,
            text_runner=lambda _prompt, _system: "",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate JEP",
        )
    )

    assert "locked" in summary.lower()
    assert key == ""
    trace = result_data.get("trace", {})
    assert trace.get("lock_outcome") == "blocked"
    assert "JEP_APPROVED_LOCKED" in trace.get("reason_codes", [])
    assert isinstance(trace.get("jep_state"), dict)

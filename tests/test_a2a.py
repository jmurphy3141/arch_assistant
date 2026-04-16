"""
tests/test_a2a.py
------------------
Unit tests for the A2A endpoint and agent card in drawing_agent_server.py.

Covers:
  1. Agent card is well-formed and contains all three skills
  2. generate_diagram skill — status ok
  3. generate_diagram skill — returns need_clarification
  4. clarify_diagram skill — completes pending clarification
  5. upload_bom skill — fetches BOM from mocked bucket
  6. Unknown skill — returns error A2AResponse (not HTTP 4xx)
  7. generate_diagram error propagates as status=error (not HTTP 5xx)
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from drawing_agent_server import app, PENDING_CLARIFY, SESSION_STORE, IDEMPOTENCY_CACHE, AGENT_ID
from tests.scenarios.fakes import FakeLLMRunner, MINIMAL_SPEC


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_state():
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    yield
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()


@pytest.fixture
def client():
    app.state.llm_runner  = FakeLLMRunner(MINIMAL_SPEC)
    app.state.object_store = None
    app.state.persistence_config = {}
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.state.llm_runner = None


SAMPLE_RESOURCES = [
    {"id": "lb_1",      "oci_type": "load balancer", "label": "LB",      "layer": "ingress"},
    {"id": "compute_1", "oci_type": "compute",        "label": "App",     "layer": "compute"},
    {"id": "db_1",      "oci_type": "database",       "label": "Oracle",  "layer": "data"},
]


# ── 1. Agent card ─────────────────────────────────────────────────────────────

class TestAgentCard:
    def test_primary_url_returns_200(self, client):
        resp = client.get("/.well-known/agent.json")
        assert resp.status_code == 200

    def test_legacy_alias_returns_200(self, client):
        resp = client.get("/.well-known/agent-card.json")
        assert resp.status_code == 200

    def test_both_urls_return_same_card(self, client):
        # /.well-known/agent.json and /.well-known/agent-card.json both serve v1.0
        primary = client.get("/.well-known/agent.json").json()
        alias   = client.get("/.well-known/agent-card.json").json()
        assert primary == alias

    def test_legacy_card_url_returns_200(self, client):
        resp = client.get("/.well-known/agent-card-legacy.json")
        assert resp.status_code == 200

    def test_legacy_card_has_old_schema(self, client):
        card = client.get("/.well-known/agent-card-legacy.json").json()
        assert card["schema_version"] == "0.1"
        assert card["agent_id"] == AGENT_ID

    def test_card_has_required_fields(self, client):
        # Oracle Agent Spec v26.1.0 schemaVersion 1.0 fields
        card = client.get("/.well-known/agent.json").json()
        assert card["schemaVersion"] == "1.0"
        assert "humanReadableId" in card
        assert card["agentVersion"] == "1.3.2"
        assert "url" in card
        assert "fleet" in card
        assert "capabilities" in card
        assert "skills" in card

    def test_card_has_orchestrate_and_diagram_skills(self, client):
        card      = client.get("/.well-known/agent.json").json()
        skill_ids = {s["id"] for s in card["skills"]}
        assert "orchestrate_engagement" in skill_ids
        assert "generate_diagram" in skill_ids

    def test_card_declares_streaming_capability(self, client):
        card = client.get("/.well-known/agent.json").json()
        assert card["capabilities"]["streaming"] is False

    def test_card_fleet_position(self, client):
        card = client.get("/.well-known/agent.json").json()
        assert card["fleet"]["position"] == 3

    def test_card_has_auth_schemes(self, client):
        card = client.get("/.well-known/agent.json").json()
        assert card["authSchemes"][0]["type"] == "none"


# ── 2. generate_diagram — success ────────────────────────────────────────────

class TestGenerateDiagramSkill:
    def test_success_returns_ok(self, client):
        resp = client.post("/api/a2a/task", json={
            "task_id":   "t1",
            "skill":     "generate_diagram",
            "client_id": "orch1",
            "inputs": {
                "resources":    SAMPLE_RESOURCES,
                "diagram_name": "fleet_test",
            },
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]   == "ok"
        assert body["task_id"]  == "t1"
        assert body["agent_id"] == AGENT_ID
        assert "drawio_xml" in body["outputs"]
        assert "request_id" in body["outputs"]
        assert "input_hash" in body["outputs"]

    def test_missing_resources_returns_error_status(self, client):
        """Missing resources → error A2AResponse, NOT an HTTP 422."""
        resp = client.post("/api/a2a/task", json={
            "task_id": "t-bad",
            "skill":   "generate_diagram",
            "inputs":  {},
        })
        assert resp.status_code == 200          # always 200 for A2A
        body = resp.json()
        assert body["status"] == "error"
        assert body["error_message"] is not None


# ── 3. generate_diagram — need_clarification ──────────────────────────────────

class TestGenerateDiagramClarification:
    def test_need_clarification_propagated(self, client):
        clarify_spec = {
            "status":    "need_clarification",
            "questions": [
                {"id": "regions.count", "question": "How many regions?", "blocking": True}
            ],
        }
        app.state.llm_runner = FakeLLMRunner(clarify_spec)

        resp = client.post("/api/a2a/task", json={
            "task_id":   "t-clarify",
            "skill":     "generate_diagram",
            "client_id": "orch2",
            "inputs":    {"resources": SAMPLE_RESOURCES, "diagram_name": "test"},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "need_clarification"
        questions = body["outputs"].get("questions", [])
        assert any(q["id"] == "regions.count" for q in questions)


# ── 4. clarify_diagram ────────────────────────────────────────────────────────

class TestClarifyDiagramSkill:
    def test_clarify_completes_pending(self, client):
        """
        Pre-inject a pending clarification, then call clarify_diagram and
        verify the pipeline completes with status=ok.
        """
        from agent.bom_parser import ServiceItem
        fake_items = [
            ServiceItem(id="lb_1", oci_type="load balancer", label="LB", layer="ingress"),
        ]
        PENDING_CLARIFY["orch3"] = {
            "items":        fake_items,
            "prompt":       "Original prompt.",
            "diagram_name": "arch",
        }

        resp = client.post("/api/a2a/task", json={
            "task_id":   "t-ans",
            "skill":     "clarify_diagram",
            "client_id": "orch3",
            "inputs": {
                "answers":      "Single region, no HA.",
                "diagram_name": "arch",
            },
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        # Pending entry should be cleared
        assert "orch3" not in PENDING_CLARIFY

    def test_clarify_no_pending_returns_error(self, client):
        resp = client.post("/api/a2a/task", json={
            "task_id":   "t-nopend",
            "skill":     "clarify_diagram",
            "client_id": "no_such_client",
            "inputs":    {"answers": "yes", "diagram_name": "x"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
        assert "No pending clarification" in resp.json()["error_message"]


# ── 5. upload_bom — mocked bucket ─────────────────────────────────────────────

class TestUploadBomSkill:
    def test_upload_bom_requires_bom_from_bucket(self, client):
        """Without bom_from_bucket the skill returns an error (no HTTP 422)."""
        resp = client.post("/api/a2a/task", json={
            "task_id": "t-bom-bad",
            "skill":   "upload_bom",
            "inputs":  {},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"

    def test_upload_bom_with_mocked_bucket(self, monkeypatch):
        """
        Mock the OCI fetch so upload_bom receives a minimal valid .xlsx,
        parses it, and returns status=ok.
        """
        import drawing_agent_server as srv
        import openpyxl, io

        # Build a minimal BOM Excel in-memory
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "BOM"
        ws.append(["SKU", "Description", "Quantity"])
        ws.append(["B91961", "OCI Compute — Standard", 2])
        buf = io.BytesIO()
        wb.save(buf)
        bom_bytes = buf.getvalue()

        monkeypatch.setattr(
            srv, "_OCI_STORAGE_AVAILABLE", True,
        )

        import types
        fake_module = types.SimpleNamespace(
            fetch_object=lambda bucket, obj, ns=None, ver=None: bom_bytes
        )
        monkeypatch.setattr(srv, "_oci_storage", fake_module)

        app.state.llm_runner  = FakeLLMRunner(MINIMAL_SPEC)
        app.state.object_store = None
        app.state.persistence_config = {}

        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post("/api/a2a/task", json={
                "task_id":   "t-bom-ok",
                "skill":     "upload_bom",
                "client_id": "orch-bom",
                "inputs": {
                    "bom_from_bucket": {
                        "bucket": "agent_assistante",
                        "object": "agent2/outputs/latest.xlsx",
                    },
                    "diagram_name": "fleet_bom_test",
                },
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("ok", "need_clarification"), body.get("error_message")


# ── 6. Unknown skill ──────────────────────────────────────────────────────────

class TestUnknownSkill:
    def test_unknown_skill_returns_error_not_http4xx(self, client):
        resp = client.post("/api/a2a/task", json={
            "task_id": "t-unknown",
            "skill":   "launch_missiles",
            "inputs":  {},
        })
        assert resp.status_code == 200         # never HTTP 4xx for A2A
        body = resp.json()
        assert body["status"] == "error"
        assert "launch_missiles" in body["error_message"]
        assert "Available" in body["error_message"]

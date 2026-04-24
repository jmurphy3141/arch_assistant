"""
tests/test_generate_questionnaire_notes.py
-------------------------------------------
Tests for /generate endpoint:
  - oci_type canonicalization and 422 on missing type
  - questionnaire / notes fields in prompt composition
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.bom_parser import freeform_arch_text_to_llm_input
from tests.scenarios.fakes import FakeLLMRunner, MINIMAL_SPEC


def _make_client(spec=None):
    from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    runner = FakeLLMRunner(spec or MINIMAL_SPEC)
    app.state.llm_runner = runner
    app.state.object_store = None
    app.state.persistence_config = {}
    return TestClient(app, raise_server_exceptions=False), runner


_RES_OCI_TYPE = [{"id": "c1", "oci_type": "compute", "label": "Compute", "layer": "compute"}]
_RES_LEGACY_TYPE = [{"id": "c1", "type": "compute", "label": "Compute", "layer": "compute"}]
_RES_MISSING_TYPE = [{"id": "c1", "label": "Compute", "layer": "compute"}]


class TestResourceTypeCanonicalisation:
    def test_accepts_oci_type_without_type(self):
        """Resources with only oci_type should succeed and produce a valid ok response."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "diagram_name": "test",
                "client_id":    "tc001",
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        # oci_type value should appear in the prompt
        assert "compute" in runner.received_prompts[0]

    def test_accepts_legacy_type_without_oci_type(self):
        """Resources with only type (no oci_type) should succeed."""
        client, _ = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_LEGACY_TYPE,
                "diagram_name": "test",
                "client_id":    "tc002",
            })
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_rejects_missing_both_type_fields_with_422(self):
        """Resource with neither oci_type nor type must return HTTP 422."""
        client, _ = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_MISSING_TYPE,
                "diagram_name": "test",
                "client_id":    "tc003",
            })
        assert resp.status_code == 422

    def test_oci_type_takes_priority_over_type(self):
        """When both oci_type and type are present, oci_type wins."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources": [
                    {"id": "r1", "oci_type": "load balancer", "type": "wrong_type", "layer": "ingress"}
                ],
                "diagram_name": "test",
                "client_id":    "tc004",
            })
        assert resp.status_code == 200
        prompt = runner.received_prompts[0]
        assert "load balancer" in prompt
        assert "wrong_type" not in prompt


class TestQuestionnaireInPrompt:
    def test_questionnaire_header_present_when_provided(self):
        """When questionnaire is non-empty, the prompt must include literal 'QUESTIONNAIRE:'."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":     _RES_OCI_TYPE,
                "questionnaire": "Single region, active-passive HA",
                "diagram_name":  "test",
                "client_id":     "tq001",
            })
        assert resp.status_code == 200
        prompt = runner.received_prompts[0]
        assert "QUESTIONNAIRE:" in prompt

    def test_questionnaire_content_included_in_prompt(self):
        """The questionnaire text itself must appear verbatim in the prompt."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":     _RES_OCI_TYPE,
                "questionnaire": "Active-passive HA across 2 ADs",
                "diagram_name":  "test",
                "client_id":     "tq002",
            })
        assert resp.status_code == 200
        assert "Active-passive HA across 2 ADs" in runner.received_prompts[0]

    def test_questionnaire_header_absent_when_not_provided(self):
        """When questionnaire is omitted, 'QUESTIONNAIRE:' must NOT appear in the prompt."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "diagram_name": "test",
                "client_id":    "tq003",
            })
        assert resp.status_code == 200
        assert "QUESTIONNAIRE:" not in runner.received_prompts[0]

    def test_empty_questionnaire_no_header(self):
        """An empty-string questionnaire must not produce the header."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":     _RES_OCI_TYPE,
                "questionnaire": "",
                "diagram_name":  "test",
                "client_id":     "tq004",
            })
        assert resp.status_code == 200
        assert "QUESTIONNAIRE:" not in runner.received_prompts[0]


class TestNotesInPrompt:
    def test_notes_header_present_when_provided(self):
        """When notes is non-empty, the prompt must include literal 'NOTES:'."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "notes":        "Customer has FastConnect to Chicago office",
                "diagram_name": "test",
                "client_id":    "tn001",
            })
        assert resp.status_code == 200
        prompt = runner.received_prompts[0]
        assert "NOTES:" in prompt

    def test_notes_content_included_in_prompt(self):
        """The notes text itself must appear verbatim in the prompt."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "notes":        "FastConnect link to Chicago",
                "diagram_name": "test",
                "client_id":    "tn002",
            })
        assert resp.status_code == 200
        assert "FastConnect link to Chicago" in runner.received_prompts[0]

    def test_notes_header_absent_when_not_provided(self):
        """When notes is omitted, 'NOTES:' must NOT appear in the prompt."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "diagram_name": "test",
                "client_id":    "tn003",
            })
        assert resp.status_code == 200
        assert "NOTES:" not in runner.received_prompts[0]

    def test_empty_notes_no_header(self):
        """An empty-string notes must not produce the header."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "notes":        "",
                "diagram_name": "test",
                "client_id":    "tn004",
            })
        assert resp.status_code == 200
        assert "NOTES:" not in runner.received_prompts[0]


class TestCombinedContextComposition:
    def test_all_three_context_fields_compose_in_order(self):
        """context, then QUESTIONNAIRE:, then NOTES: — all present and in correct order."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":     _RES_OCI_TYPE,
                "context":       "6-region deployment",
                "questionnaire": "Active-passive",
                "notes":         "Low latency required",
                "diagram_name":  "test",
                "client_id":     "tcc001",
            })
        assert resp.status_code == 200
        prompt = runner.received_prompts[0]
        assert "6-region deployment" in prompt
        assert "QUESTIONNAIRE:" in prompt
        assert "Active-passive" in prompt
        assert "NOTES:" in prompt
        assert "Low latency required" in prompt
        # Order: QUESTIONNAIRE must appear before NOTES
        assert prompt.index("QUESTIONNAIRE:") < prompt.index("NOTES:")

    def test_context_without_questionnaire_or_notes(self):
        """context alone (no questionnaire/notes) still works and no extra headers appear."""
        client, runner = _make_client()
        with client:
            resp = client.post("/generate", json={
                "resources":    _RES_OCI_TYPE,
                "context":      "HA deployment",
                "diagram_name": "test",
                "client_id":    "tcc002",
            })
        assert resp.status_code == 200
        prompt = runner.received_prompts[0]
        assert "HA deployment" in prompt
        assert "QUESTIONNAIRE:" not in prompt
        assert "NOTES:" not in prompt


class TestFreeformInference:
    def test_ha_web_server_notes_infer_compute_and_load_balancer(self):
        items, prompt = freeform_arch_text_to_llm_input("BOM and Diagram for a small HA web server")

        oci_types = {item.oci_type for item in items}
        assert "compute" in oci_types
        assert "load balancer" in oci_types
        assert "small HA web server" in prompt

    def test_ha_web_server_typo_still_infers_minimum_workload(self):
        items, _prompt = freeform_arch_text_to_llm_input("BOM and Diagram for a small HA web serer")

        oci_types = {item.oci_type for item in items}
        assert "compute" in oci_types
        assert "load balancer" in oci_types

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

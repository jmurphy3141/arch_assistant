"""
tests/test_inference_runner.py
-------------------------------
Unit tests for the OCI Inference backend integration.

Monkeypatches agent.llm_inference_client.run_inference to avoid real OCI calls.

Tests:
  1. Plain JSON response → parsed successfully
  2. Fenced JSON response (```json ... ```) → parsed successfully
  3. Non-JSON response → HTTP 422 from the /generate endpoint
"""
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from drawing_agent_server import app, PENDING_CLARIFY, SESSION_STORE, IDEMPOTENCY_CACHE
from tests.scenarios.fakes import FakeLLMRunner, MINIMAL_SPEC


# ── Helpers ─────────────────────────────────────────────────────────────────────

SAMPLE_RESOURCES = [
    {"id": "compute_1", "oci_type": "compute",  "label": "App Server", "layer": "compute"},
    {"id": "db_1",      "oci_type": "database",  "label": "Oracle DB",  "layer": "data"},
]


@pytest.fixture(autouse=True)
def clear_state():
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    yield
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()


def _make_client(runner):
    app.state.llm_runner = runner
    app.state.object_store = None
    app.state.persistence_config = {}
    return TestClient(app, raise_server_exceptions=True)


# ── Inference runner unit tests ──────────────────────────────────────────────────

class TestInferenceRunnerTextParsing:
    """
    Verify that _make_inference_runner correctly handles the three
    text shapes that run_inference() might return.

    We monkeypatch run_inference at the module level in drawing_agent_server
    via a custom runner that simulates what _make_inference_runner does:
    accept raw text → clean_json → json.loads.
    """

    def test_plain_json_parses(self, monkeypatch):
        """Plain JSON string → parsed dict without error."""
        import drawing_agent_server as srv

        raw_text = json.dumps(MINIMAL_SPEC)
        captured = {}

        def fake_run_inference(prompt, *, endpoint, model_id, compartment_id,
                               max_tokens, temperature, top_p, top_k,
                               system_message=""):
            captured["called"] = True
            captured["system_message"] = system_message
            return raw_text

        monkeypatch.setattr(srv, "_run_inference", fake_run_inference)
        monkeypatch.setattr(srv, "INFERENCE_ENABLED", True)
        monkeypatch.setattr(srv, "_INFERENCE_AVAILABLE", True)
        monkeypatch.setattr(srv, "INFERENCE_SYSTEM_MSG", "You are a test assistant.")

        runner = srv._make_inference_runner()
        result = runner("test prompt", "client1")
        assert isinstance(result, dict)
        assert result.get("deployment_type") == "single_ad"
        # system_message must be forwarded
        assert captured["system_message"] == "You are a test assistant."

    def test_system_message_forwarded_to_run_inference(self, monkeypatch):
        """INFERENCE_SYSTEM_MSG from config is passed as system_message kwarg."""
        import drawing_agent_server as srv

        received = {}

        def capturing_run_inference(prompt, *, system_message="", **kw):
            received["system_message"] = system_message
            return json.dumps(MINIMAL_SPEC)

        monkeypatch.setattr(srv, "_run_inference", capturing_run_inference)
        monkeypatch.setattr(srv, "INFERENCE_ENABLED", True)
        monkeypatch.setattr(srv, "_INFERENCE_AVAILABLE", True)
        monkeypatch.setattr(srv, "INFERENCE_SYSTEM_MSG",
                            "Output ONLY valid JSON. No markdown.")

        runner = srv._make_inference_runner()
        runner("any prompt", "any_client")
        assert received["system_message"] == "Output ONLY valid JSON. No markdown."

    def test_empty_system_message_when_not_configured(self, monkeypatch):
        """If INFERENCE_SYSTEM_MSG is empty, system_message kwarg is empty string."""
        import drawing_agent_server as srv

        received = {}

        def capturing_run_inference(prompt, *, system_message="", **kw):
            received["system_message"] = system_message
            return json.dumps(MINIMAL_SPEC)

        monkeypatch.setattr(srv, "_run_inference", capturing_run_inference)
        monkeypatch.setattr(srv, "INFERENCE_ENABLED", True)
        monkeypatch.setattr(srv, "_INFERENCE_AVAILABLE", True)
        monkeypatch.setattr(srv, "INFERENCE_SYSTEM_MSG", "")

        runner = srv._make_inference_runner()
        runner("any prompt", "any_client")
        assert received["system_message"] == ""

    def test_fenced_json_parses(self, monkeypatch):
        """Fenced ```json ... ``` response → clean_json strips fences → parsed dict."""
        import drawing_agent_server as srv

        fenced = f"```json\n{json.dumps(MINIMAL_SPEC)}\n```"

        monkeypatch.setattr(
            srv,
            "_run_inference",
            lambda prompt, *, system_message="", **kw: fenced,
        )
        monkeypatch.setattr(srv, "INFERENCE_ENABLED", True)
        monkeypatch.setattr(srv, "_INFERENCE_AVAILABLE", True)

        runner = srv._make_inference_runner()
        result = runner("test prompt", "client1")
        assert isinstance(result, dict)
        assert "regions" in result

    def test_non_json_raises_422(self, monkeypatch):
        """Non-JSON model output → _make_inference_runner raises HTTPException 422."""
        import drawing_agent_server as srv
        from fastapi import HTTPException

        monkeypatch.setattr(
            srv,
            "_run_inference",
            lambda prompt, *, system_message="", **kw: "Sorry, I cannot generate a diagram for that.",
        )
        monkeypatch.setattr(srv, "INFERENCE_ENABLED", True)
        monkeypatch.setattr(srv, "_INFERENCE_AVAILABLE", True)

        runner = srv._make_inference_runner()
        with pytest.raises(HTTPException) as exc_info:
            runner("test prompt", "client1")
        assert exc_info.value.status_code == 422
        assert "valid JSON" in exc_info.value.detail


# ── End-to-end via TestClient ─────────────────────────────────────────────────────

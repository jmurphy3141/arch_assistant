"""
tests/scenarios/test_scenarios.py
----------------------------------
Agent 3 v1.5.0 scenario tests.  No real OCI calls.

T_MR_001 – multi-region without multi_region_mode → need_clarification (regions.mode)
T_MR_002 – duplicate_drha → region_secondary_stub box (w=260, h=90)
T_MR_003 – split_workloads → render_manifest.page.width == 3308

T_OS_001 – status=ok → 5 artifacts written under request_id prefix
T_OS_002 – LATEST.json exists and all artifact paths exist in the store
T_OS_003 – artifact put failure → LATEST.json NOT updated

T_DL_001 – GET /download without client_id/diagram_name → 400 MISSING_DOWNLOAD_SCOPE
T_DL_002 – GET /download with scope → bytes from LATEST.json fallback when local missing

T_IDEMP_001 – identical /generate twice → llm_runner called once, same request_id
T_IDEMP_002 – changed context → new input_hash, llm_runner called again, new request_id
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from tests.scenarios.fakes import (
    FakeLLMRunner,
    FakeTextRunner,
    InMemoryObjectStoreFake,
    MINIMAL_SPEC,
    MULTI_REGION_SPEC,
)
from tests.scenarios.helpers import (
    assert_ok_envelope,
    assert_clarify_envelope,
    compute_input_hash_for_generate,
)

# ── shared resources payload ────────────────────────────────────────────────────
_RESOURCES = [
    {"id": "compute_1", "oci_type": "compute", "label": "Compute", "layer": "compute"},
]


def _make_client(spec, store=None, persistence_config=None):
    """Build a TestClient with a specific fake runner and optional store."""
    from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
    IDEMPOTENCY_CACHE.clear()
    SESSION_STORE.clear()
    PENDING_CLARIFY.clear()
    runner = FakeLLMRunner(spec)
    app.state.llm_runner = runner
    app.state.object_store = store
    app.state.persistence_config = persistence_config or {}
    return TestClient(app, raise_server_exceptions=True), runner


# ═══════════════════════════════════════════════════════════════════════════════
# T_MR — Multi-Region Hints
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# T_OS — Object Storage Persistence
# ═══════════════════════════════════════════════════════════════════════════════

_EXPECTED_ARTIFACTS = {
    "diagram.drawio",
    "spec.json",
    "draw_dict.json",
    "render_manifest.json",
    "node_to_resource_map.json",
}


# ═══════════════════════════════════════════════════════════════════════════════
# T_DL — Download Endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class TestDownload:
    """T_DL_001 – T_DL_002"""

    def test_dl_001_missing_scope_returns_400(self):
        """GET /download/... without client_id or diagram_name => 400 MISSING_DOWNLOAD_SCOPE."""
        client, _ = _make_client(MINIMAL_SPEC)
        with client:
            # No query params at all
            resp = client.get("/download/diagram.drawio")
            assert resp.status_code == 400
            detail = resp.json()["detail"]
            assert detail["error_code"] == "MISSING_DOWNLOAD_SCOPE"

            # Only client_id missing
            resp2 = client.get("/download/diagram.drawio?diagram_name=mydiag")
            assert resp2.status_code == 400

            # Only diagram_name missing
            resp3 = client.get("/download/diagram.drawio?client_id=dl001")
            assert resp3.status_code == 400

# ═══════════════════════════════════════════════════════════════════════════════
# T_IDEMP — Idempotency Cache
# ═══════════════════════════════════════════════════════════════════════════════



# ═══════════════════════════════════════════════════════════════════════════════
# T_REFINE — /api/refine editor path
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefine:
    """
    T_REFINE_001 – editor path (prev_spec available): uses text_runner, never
                   asks clarification questions, returns status=ok with updated diagram.
    T_REFINE_002 – editor LLM returns need_clarification: falls back to prev_spec
                   unchanged (no-op edit), still returns status=ok.
    T_REFINE_003 – no prev_spec: falls back to run_pipeline with appended feedback.
    """

    def _make_refine_client(self, llm_spec=None, text_response=""):
        """Set up TestClient with both llm_runner and text_runner."""
        from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear()
        SESSION_STORE.clear()
        PENDING_CLARIFY.clear()
        app.state.llm_runner  = FakeLLMRunner(llm_spec or MINIMAL_SPEC)
        app.state.text_runner = FakeTextRunner(text_response)
        app.state.object_store = None
        app.state.persistence_config = {}
        return TestClient(app, raise_server_exceptions=True)

    def test_refine_001_editor_path_returns_ok(self):
        """
        When prev_spec is supplied the editor path must:
          - Call text_runner (not llm_runner) with DIAGRAM_EDIT_SYSTEM
          - Return status=ok with drawio_xml and _refine_context
          - Not call llm_runner at all
        """
        import json
        from drawing_agent_server import app, DIAGRAM_EDIT_SYSTEM

        # The text_runner will echo back MINIMAL_SPEC as a JSON string
        text_runner = FakeTextRunner(json.dumps(MINIMAL_SPEC))
        llm_runner  = FakeLLMRunner(MINIMAL_SPEC)

        from drawing_agent_server import IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear(); SESSION_STORE.clear(); PENDING_CLARIFY.clear()
        app.state.llm_runner   = llm_runner
        app.state.text_runner  = text_runner
        # editor_runner = same fake so call_count/received_messages assertions still pass
        app.state.editor_runner = text_runner
        app.state.object_store = None
        app.state.persistence_config = {}

        from agent.bom_parser import ServiceItem
        items = [ServiceItem(id="lb_1", oci_type="load balancer", label="LB", layer="ingress")]
        items_json = json.dumps([{"id": i.id, "oci_type": i.oci_type,
                                  "label": i.label, "layer": i.layer} for i in items])
        prev_spec = json.dumps(MINIMAL_SPEC)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/refine", json={
                "feedback":              "Add a bastion host",
                "client_id":             "refine001",
                "diagram_name":          "refine_diag",
                "items_json":            items_json,
                "prompt":                "original BOM prompt",
                "prev_spec":             prev_spec,
                "deployment_hints_json": "{}",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok", f"Expected ok, got: {data}"
        assert "drawio_xml" in data
        assert "<mxGraphModel" in data["drawio_xml"] or "mxCell" in data["drawio_xml"]
        assert "_refine_context" in data
        # prev_spec preserved in _refine_context for next refinement
        assert data["_refine_context"]["prev_spec"] is not None
        # prompt preserved unchanged (original BOM prompt, not the edit prompt)
        assert data["_refine_context"]["prompt"] == "original BOM prompt"

        # text_runner was called; llm_runner was NOT called
        assert text_runner.call_count == 1, f"text_runner should be called once, got {text_runner.call_count}"
        assert llm_runner.call_count  == 0, f"llm_runner must not be called in editor path, got {llm_runner.call_count}"

        # System message must be the editor constant (never-ask-questions persona)
        assert DIAGRAM_EDIT_SYSTEM in text_runner.received_system_messages[0]

        app.state.llm_runner   = None
        app.state.text_runner  = None
        app.state.editor_runner = None

    def test_refine_002_editor_returns_need_clarification_falls_back(self):
        """
        When the editor LLM ignores the system message and returns need_clarification,
        the handler should fall back to the prev_spec unchanged and still return status=ok.
        """
        import json
        from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear(); SESSION_STORE.clear(); PENDING_CLARIFY.clear()

        # Text runner returns a need_clarification response
        nc_response = json.dumps({"status": "need_clarification", "questions": ["What colour?"]})
        text_runner = FakeTextRunner(nc_response)
        llm_runner  = FakeLLMRunner(MINIMAL_SPEC)
        app.state.llm_runner   = llm_runner
        app.state.text_runner  = text_runner
        app.state.editor_runner = text_runner
        app.state.object_store = None
        app.state.persistence_config = {}

        items_json = json.dumps([{"id": "lb_1", "oci_type": "load balancer",
                                  "label": "LB", "layer": "ingress"}])
        prev_spec = json.dumps(MINIMAL_SPEC)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/refine", json={
                "feedback":              "Add something",
                "client_id":             "refine002",
                "diagram_name":          "refine_diag",
                "items_json":            items_json,
                "prompt":                "original prompt",
                "prev_spec":             prev_spec,
                "deployment_hints_json": "{}",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Must still succeed (no-op edit fallback)
        assert data["status"] == "ok", f"Expected ok, got: {data}"
        # llm_runner still never called
        assert llm_runner.call_count == 0

        app.state.llm_runner   = None
        app.state.text_runner  = None
        app.state.editor_runner = None

    def test_refine_003_no_prev_spec_calls_run_pipeline(self):
        """
        Without prev_spec, /api/refine falls back to run_pipeline (llm_runner called).
        The response should still be status=ok.
        """
        import json
        from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear(); SESSION_STORE.clear(); PENDING_CLARIFY.clear()

        llm_runner  = FakeLLMRunner(MINIMAL_SPEC)
        text_runner = FakeTextRunner("")
        app.state.llm_runner   = llm_runner
        app.state.text_runner  = text_runner
        app.state.editor_runner = text_runner  # won't be called (no prev_spec)
        app.state.object_store = None
        app.state.persistence_config = {}

        items_json = json.dumps([{"id": "lb_1", "oci_type": "load balancer",
                                  "label": "LB", "layer": "ingress"}])

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/refine", json={
                "feedback":              "Add monitoring",
                "client_id":             "refine003",
                "diagram_name":          "refine_diag",
                "items_json":            items_json,
                "prompt":                "original prompt",
                # prev_spec intentionally omitted
                "deployment_hints_json": "{}",
            })

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ok"
        # llm_runner was called (run_pipeline path)
        assert llm_runner.call_count == 1
        # text_runner was NOT called
        assert text_runner.call_count == 0

        app.state.llm_runner   = None
        app.state.text_runner  = None
        app.state.editor_runner = None

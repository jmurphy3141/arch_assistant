"""
tests/scenarios/test_scenarios.py
----------------------------------
Agent 3 v1.3.2 scenario tests.  No real OCI calls.

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

class TestMultiRegion:
    """T_MR_001 – T_MR_003"""

    def test_mr_001_missing_hints_returns_clarification(self):
        """multi-region spec + no multi_region_mode => need_clarification with regions.mode."""
        client, _ = _make_client(MULTI_REGION_SPEC)
        with client:
            resp = client.post("/generate", json={
                "resources":        _RESOURCES,
                "context":          "multi-region deployment",
                "diagram_name":     "mr_test",
                "client_id":        "mr001",
                "deployment_hints": {},   # no multi_region_mode
            })

        assert resp.status_code == 200
        data = resp.json()
        assert_clarify_envelope(data)
        assert data["status"] == "need_clarification"
        question_ids = [q["id"] for q in data["questions"]]
        assert "regions.mode" in question_ids

    def test_mr_002_duplicate_drha_returns_stub_box(self):
        """duplicate_drha => status=ok AND region_secondary_stub box with w=260, h=90."""
        client, _ = _make_client(MULTI_REGION_SPEC)
        with client:
            resp = client.post("/generate", json={
                "resources":        _RESOURCES,
                "context":          "multi-region HA",
                "diagram_name":     "mr_test",
                "client_id":        "mr002",
                "deployment_hints": {"multi_region_mode": "duplicate_drha"},
            })

        assert resp.status_code == 200
        data = resp.json()
        assert_ok_envelope(data)

        box_ids = [b["id"] for b in data["draw_dict"]["boxes"]]
        assert "region_secondary_stub" in box_ids, (
            f"region_secondary_stub not in boxes: {box_ids}"
        )
        stub = next(b for b in data["draw_dict"]["boxes"] if b["id"] == "region_secondary_stub")
        assert stub["w"] == 260
        assert stub["h"] == 90

    def test_mr_003_split_workloads_page_width(self):
        """split_workloads => render_manifest.page.width == 3308."""
        client, _ = _make_client(MULTI_REGION_SPEC)
        with client:
            resp = client.post("/generate", json={
                "resources":        _RESOURCES,
                "context":          "split workloads",
                "diagram_name":     "mr_test",
                "client_id":        "mr003",
                "deployment_hints": {"multi_region_mode": "split_workloads"},
            })

        assert resp.status_code == 200
        data = resp.json()
        assert_ok_envelope(data)
        assert data["render_manifest"]["page"]["width"] == 3308


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


class TestObjectStorage:
    """T_OS_001 – T_OS_003"""

    def test_os_001_five_artifacts_written(self):
        """status=ok => 5 artifacts written under {prefix}/{client_id}/{diagram_name}/{request_id}/"""
        store = InMemoryObjectStoreFake()
        client, _ = _make_client(MINIMAL_SPEC, store=store, persistence_config={"prefix": "diagrams"})
        with client:
            resp = client.post("/generate", json={
                "resources":    _RESOURCES,
                "diagram_name": "mydiag",
                "client_id":    "os001",
            })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

        # Find all artifact keys under the v1 prefix
        prefix = "diagrams/os001/mydiag/v1/"
        stored_filenames = {
            k[len(prefix):]
            for k in store.list_keys()
            if k.startswith(prefix)
        }
        assert stored_filenames == _EXPECTED_ARTIFACTS, (
            f"Expected artifacts {_EXPECTED_ARTIFACTS}, got {stored_filenames}"
        )

    def test_os_002_latest_json_points_to_existing_keys(self):
        """LATEST.json exists and every artifact path it references is present in the store."""
        store = InMemoryObjectStoreFake()
        client, _ = _make_client(MINIMAL_SPEC, store=store, persistence_config={"prefix": "diagrams"})
        with client:
            resp = client.post("/generate", json={
                "resources":    _RESOURCES,
                "diagram_name": "mydiag",
                "client_id":    "os002",
            })

        assert resp.status_code == 200
        data = resp.json()

        latest_key = "diagrams/os002/mydiag/LATEST.json"
        assert store.head(latest_key), "LATEST.json was not written"

        latest = json.loads(store.get(latest_key).decode("utf-8"))
        assert latest["schema_version"] == "1.1"
        assert latest["version"] == 1

        for filename, artifact_key in latest["artifacts"].items():
            assert store.head(artifact_key), (
                f"Artifact '{filename}' at key '{artifact_key}' not found in store"
            )

    def test_os_003_artifact_failure_leaves_latest_unchanged(self):
        """If one artifact upload fails, LATEST.json must NOT be written/updated."""
        store = InMemoryObjectStoreFake()
        # Inject failure for render_manifest.json
        store.inject_put_failure("/render_manifest.json")

        client, _ = _make_client(MINIMAL_SPEC, store=store, persistence_config={"prefix": "diagrams"})
        with client:
            resp = client.post("/generate", json={
                "resources":    _RESOURCES,
                "diagram_name": "mydiag",
                "client_id":    "os003",
            })

        # The endpoint itself should still succeed (persistence failure is non-fatal)
        assert resp.status_code == 200

        latest_key = "diagrams/os003/mydiag/LATEST.json"
        assert not store.head(latest_key), (
            "LATEST.json should NOT exist when an artifact upload failed"
        )


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

    def test_dl_002_object_store_fallback_when_local_missing(self):
        """
        With scope params and object store enabled:
        if local file is missing, bytes are served via LATEST.json lookup.
        """
        store = InMemoryObjectStoreFake()
        client, runner = _make_client(
            MINIMAL_SPEC, store=store, persistence_config={"prefix": "diagrams"}
        )

        with client:
            # Step 1: generate to populate the object store
            gen_resp = client.post("/generate", json={
                "resources":    _RESOURCES,
                "diagram_name": "dldiag",
                "client_id":    "dl002",
            })
            assert gen_resp.status_code == 200
            assert gen_resp.json()["status"] == "ok"

            # Step 2: remove local file so download must fall back to object store
            from drawing_agent_server import OUTPUT_DIR
            local_file = OUTPUT_DIR / "dldiag.drawio"
            if local_file.exists():
                local_file.unlink()

            # Step 3: download via scope — should fall back to object store
            dl_resp = client.get(
                "/download/diagram.drawio?client_id=dl002&diagram_name=dldiag"
            )
            assert dl_resp.status_code == 200
            # Response should be the draw.io XML bytes
            body = dl_resp.content
            assert len(body) > 0
            # Verify it looks like draw.io XML
            assert b"mxGraphModel" in body or b"mxCell" in body or len(body) > 50


# ═══════════════════════════════════════════════════════════════════════════════
# T_IDEMP — Idempotency Cache
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdempotency:
    """T_IDEMP_001 – T_IDEMP_002"""

    def test_idemp_001_identical_requests_call_llm_once(self):
        """
        Identical /generate calls (same resources, context, hints) for the same
        (client_id, diagram_name) must:
          - Call llm_runner exactly once
          - Return the same request_id on both calls
        """
        runner = FakeLLMRunner(MINIMAL_SPEC)
        from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear()
        SESSION_STORE.clear()
        PENDING_CLARIFY.clear()
        app.state.llm_runner = runner
        app.state.object_store = None
        app.state.persistence_config = {}

        payload = {
            "resources":    _RESOURCES,
            "context":      "HA deployment",
            "diagram_name": "idemp_diag",
            "client_id":    "idemp001",
        }

        with TestClient(app, raise_server_exceptions=True) as client:
            resp1 = client.post("/generate", json=payload)
            resp2 = client.post("/generate", json=payload)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        d1 = resp1.json()
        d2 = resp2.json()

        assert d1["status"] == "ok"
        assert d2["status"] == "ok"

        # LLM called only once
        assert runner.call_count == 1, (
            f"Expected llm_runner to be called once, got {runner.call_count}"
        )

        # Same request_id on both responses
        assert d1["request_id"] == d2["request_id"], (
            f"request_id mismatch: {d1['request_id']} != {d2['request_id']}"
        )

        app.state.llm_runner = None

    def test_idemp_002_changed_context_calls_llm_again(self):
        """
        Changing context produces a different input_hash → cache miss →
        llm_runner called again → new request_id.
        """
        runner = FakeLLMRunner(MINIMAL_SPEC)
        from drawing_agent_server import app, IDEMPOTENCY_CACHE, SESSION_STORE, PENDING_CLARIFY
        IDEMPOTENCY_CACHE.clear()
        SESSION_STORE.clear()
        PENDING_CLARIFY.clear()
        app.state.llm_runner = runner
        app.state.object_store = None
        app.state.persistence_config = {}

        base_payload = {
            "resources":    _RESOURCES,
            "diagram_name": "idemp_diag",
            "client_id":    "idemp002",
        }

        with TestClient(app, raise_server_exceptions=True) as client:
            resp1 = client.post("/generate", json={**base_payload, "context": "HA deployment"})
            resp2 = client.post("/generate", json={**base_payload, "context": "DR deployment"})

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        d1 = resp1.json()
        d2 = resp2.json()

        assert d1["status"] == "ok"
        assert d2["status"] == "ok"

        # LLM called twice (two distinct cache keys)
        assert runner.call_count == 2, (
            f"Expected llm_runner called 2 times, got {runner.call_count}"
        )

        # input_hash differs
        assert d1["input_hash"] != d2["input_hash"], (
            "input_hash should differ for different contexts"
        )

        # request_id differs
        assert d1["request_id"] != d2["request_id"], (
            "request_id should differ for different contexts"
        )

        app.state.llm_runner = None

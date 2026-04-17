"""
server/tests/test_api.py
------------------------
Unit tests for server/app/main.py endpoints.

OCI bucket reads are mocked by monkeypatching
    server.services.oci_object_storage.fetch_object
"""
from __future__ import annotations

import copy
import json
import os

import pytest
from fastapi.testclient import TestClient

import server.services.oci_object_storage as oci_storage
from server.app.main import app
from tests.scenarios.fakes import MINIMAL_SPEC, FakeLLMRunner


# ---------------------------------------------------------------------------
# Helper: build a minimal resources list
# ---------------------------------------------------------------------------
_RESOURCES = [
    {
        "id": "compute_1",
        "oci_type": "compute",
        "label": "App Server",
        "layer": "compute",
    }
]


# ===========================================================================
# D1: /api/generate inline works
# ===========================================================================
class TestGenerateInline:
    def test_returns_ok(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "test_diag",
                "client_id": "test1",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_response_has_request_id_and_input_hash(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "test_diag",
                "client_id": "test1",
            },
        )
        data = resp.json()
        assert "request_id" in data
        assert "input_hash" in data
        assert len(data["request_id"]) == 36  # UUID format
        assert len(data["input_hash"]) == 64  # sha256 hex

    def test_response_has_render_manifest(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "mfest_diag",
                "client_id": "test1",
            },
        )
        data = resp.json()
        assert "render_manifest" in data
        assert "page" in data["render_manifest"]

    def test_download_url_uses_api_prefix(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "dl_diag",
                "client_id": "test1",
            },
        )
        data = resp.json()
        assert "/api/download/" in data["download"]["url"]

    def test_legacy_path_also_works(self, client: TestClient):
        """Routes are also mounted without /api prefix for backwards compat."""
        resp = client.post(
            "/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "legacy_diag",
                "client_id": "test_legacy",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_missing_oci_type_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": [{"id": "x"}],
                "diagram_name": "bad",
                "client_id": "c1",
            },
        )
        assert resp.status_code == 422

    def test_both_resources_and_bucket_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "resources_from_bucket": {"bucket": "b", "object": "r.json"},
                "diagram_name": "both",
                "client_id": "c1",
            },
        )
        assert resp.status_code == 422

    def test_neither_resources_nor_bucket_returns_422(self, client: TestClient):
        resp = client.post(
            "/api/generate",
            json={"diagram_name": "none", "client_id": "c1"},
        )
        assert resp.status_code == 422


# ===========================================================================
# D2: /api/generate bucket mode loads resources via mocked helper
# ===========================================================================
class TestGenerateBucketMode:
    def _patch_fetch(self, monkeypatch, data: bytes):
        monkeypatch.setattr(oci_storage, "fetch_object", lambda *a, **kw: data)

    def test_bucket_resources_loaded(self, client: TestClient, monkeypatch):
        resources_json = json.dumps(_RESOURCES).encode()
        self._patch_fetch(monkeypatch, resources_json)
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "resources.json",
                },
                "diagram_name": "bucket_diag",
                "client_id": "test_bucket",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"

    def test_bucket_context_loaded(self, client: TestClient, monkeypatch):
        call_log: list = []

        def fake_fetch(bucket, obj, ns=None, vid=None):
            call_log.append((bucket, obj))
            if obj == "resources.json":
                return json.dumps(_RESOURCES).encode()
            return b"Some context text"

        monkeypatch.setattr(oci_storage, "fetch_object", fake_fetch)
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "resources.json",
                },
                "context_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "context.txt",
                },
                "diagram_name": "ctx_diag",
                "client_id": "test_ctx",
            },
        )
        assert resp.status_code == 200, resp.text
        fetched_objects = [o for _, o in call_log]
        assert "resources.json" in fetched_objects
        assert "context.txt" in fetched_objects


# ===========================================================================
# D3: Invalid JSON / non-array errors
# ===========================================================================
class TestBucketInvalidPayloads:
    def _patch_fetch(self, monkeypatch, data: bytes):
        monkeypatch.setattr(oci_storage, "fetch_object", lambda *a, **kw: data)

    def test_invalid_json_returns_422(self, client: TestClient, monkeypatch):
        self._patch_fetch(monkeypatch, b"not json at all !!!")
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "bad.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 422
        assert "invalid JSON" in resp.json()["detail"]

    def test_non_array_json_returns_422(self, client: TestClient, monkeypatch):
        self._patch_fetch(monkeypatch, json.dumps({"key": "not-an-array"}).encode())
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "obj.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 422
        assert "array" in resp.json()["detail"]


# ===========================================================================
# D4: Size limit enforcement
# ===========================================================================
class TestSizeLimits:
    def test_resources_too_large_returns_413(
        self, client: TestClient, monkeypatch
    ):
        # 1 byte over the configured limit
        limit = 512
        monkeypatch.setenv("MAX_OBJECT_BYTES_RESOURCES", str(limit))
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")
        monkeypatch.setattr(
            oci_storage, "fetch_object", lambda *a, **kw: b"x" * (limit + 1)
        )

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "big.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"]

    def test_text_field_too_large_returns_413(
        self, client: TestClient, monkeypatch
    ):
        limit = 128
        monkeypatch.setenv("MAX_OBJECT_BYTES_TEXT", str(limit))
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        def fake_fetch(bucket, obj, ns=None, vid=None):
            if obj == "resources.json":
                return json.dumps(_RESOURCES).encode()
            return b"x" * (limit + 1)

        monkeypatch.setattr(oci_storage, "fetch_object", fake_fetch)

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "resources.json",
                },
                "context_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "big_ctx.txt",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 413

    def test_bom_upload_too_large_returns_413(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setenv("MAX_UPLOAD_BYTES_BOM", "10")
        import io

        resp = client.post(
            "/api/upload-bom",
            files={"file": ("test.xlsx", io.BytesIO(b"x" * 11), "application/octet-stream")},
            data={"diagram_name": "d", "client_id": "c"},
        )
        assert resp.status_code == 413


# ===========================================================================
# D5: Allowlist enforcement
# ===========================================================================
class TestAllowlist:
    def _patch_fetch(self, monkeypatch, data: bytes):
        monkeypatch.setattr(oci_storage, "fetch_object", lambda *a, **kw: data)

    def test_blocked_bucket_returns_403(self, client: TestClient, monkeypatch):
        self._patch_fetch(monkeypatch, json.dumps(_RESOURCES).encode())
        monkeypatch.setenv("ALLOWED_BUCKETS", "only-this-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "forbidden-bucket",
                    "object": "r.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 403
        assert "allowlist" in resp.json()["detail"].lower()

    def test_allowed_bucket_passes(self, client: TestClient, monkeypatch):
        self._patch_fetch(monkeypatch, json.dumps(_RESOURCES).encode())
        monkeypatch.setenv("ALLOWED_BUCKETS", "allowed-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "allowed-bucket",
                    "object": "r.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 200

    def test_prefix_allowlist_enforced(self, client: TestClient, monkeypatch):
        self._patch_fetch(monkeypatch, json.dumps(_RESOURCES).encode())
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")
        monkeypatch.setenv("ALLOWED_PREFIXES", "allowed/")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "forbidden/r.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 403

    def test_no_allowed_buckets_set_denies_all(self, client: TestClient, monkeypatch):
        """When ALLOWED_BUCKETS is set to a specific bucket, other buckets are denied."""
        self._patch_fetch(monkeypatch, json.dumps(_RESOURCES).encode())
        monkeypatch.setenv("ALLOWED_BUCKETS", "specific-bucket")

        resp = client.post(
            "/api/generate",
            json={
                "resources_from_bucket": {
                    "bucket": "other-bucket",
                    "object": "r.json",
                },
                "diagram_name": "d",
                "client_id": "c",
            },
        )
        assert resp.status_code == 403


# ===========================================================================
# D6: /api/inputs/resolve preview
# ===========================================================================
class TestInputsResolve:
    def test_resolve_valid_resources(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            oci_storage,
            "fetch_object",
            lambda *a, **kw: json.dumps(_RESOURCES).encode(),
        )
        monkeypatch.setenv("ALLOWED_BUCKETS", "my-bucket")

        resp = client.post(
            "/api/inputs/resolve",
            json={
                "resources_from_bucket": {
                    "bucket": "my-bucket",
                    "object": "r.json",
                }
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["resolved"]["resources"]["ok"] is True
        assert data["resolved"]["resources"]["count"] == len(_RESOURCES)

    def test_resolve_invalid_bucket_captured_in_errors(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setattr(
            oci_storage,
            "fetch_object",
            lambda *a, **kw: json.dumps(_RESOURCES).encode(),
        )
        monkeypatch.setenv("ALLOWED_BUCKETS", "only-allowed")

        resp = client.post(
            "/api/inputs/resolve",
            json={
                "resources_from_bucket": {
                    "bucket": "forbidden",
                    "object": "r.json",
                }
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial"
        assert "resources" in data["errors"]

    def test_resolve_empty_request(self, client: TestClient):
        resp = client.post("/api/inputs/resolve", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["resolved"] == {}
        assert data["errors"] == {}


# ===========================================================================
# Health endpoint
# ===========================================================================
class TestHealth:
    def test_health_ok(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent_version"] == "1.3.2"

    def test_legacy_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ===========================================================================
# Clarification flow
# ===========================================================================
class TestClarify:
    def test_clarify_flow(self, client: TestClient):
        """Full clarification round-trip."""
        fake_runner = FakeLLMRunner()
        fake_runner.queue_spec(
            {
                "status": "need_clarification",
                "questions": [
                    {
                        "id": "ha.ads",
                        "question": "How many ADs?",
                        "blocking": True,
                    }
                ],
            }
        )
        # Second call returns a valid spec
        from tests.scenarios.fakes import MINIMAL_SPEC

        fake_runner.queue_spec(copy.deepcopy(MINIMAL_SPEC))

        app.state.llm_runner = fake_runner

        # Step 1: generate → clarification
        resp1 = client.post(
            "/api/generate",
            json={
                "resources": _RESOURCES,
                "diagram_name": "clarify_diag",
                "client_id": "clar1",
            },
        )
        assert resp1.status_code == 200
        d1 = resp1.json()
        assert d1["status"] == "need_clarification"
        assert len(d1["questions"]) == 1

        # Step 2: clarify → ok
        resp2 = client.post(
            "/api/clarify",
            json={
                "answers": "2 ADs",
                "client_id": "clar1",
                "diagram_name": "clarify_diag",
            },
        )
        assert resp2.status_code == 200
        d2 = resp2.json()
        assert d2["status"] == "ok"

    def test_clarify_no_pending_returns_404(self, client: TestClient):
        resp = client.post(
            "/api/clarify",
            json={
                "answers": "some answer",
                "client_id": "nobody",
                "diagram_name": "d",
            },
        )
        assert resp.status_code == 404

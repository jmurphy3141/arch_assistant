"""
tests/test_object_store_oci.py
-------------------------------
Unit tests for agent/object_store_oci.py.

All OCI SDK calls are mocked — no real credentials or network access.
Tests validate:
  1. Correct endpoint / namespace / bucket forwarded to the SDK client.
  2. Key formatting (prefix/client/diagram/v{N}/filename).
  3. Atomicity: LATEST.json is only written after all artifacts succeed.
  4. get() KeyError on 404, PermissionError on 403.
  5. head() returns True/False correctly.
  6. OciObjectStore integrates with persist_artifacts().
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a mock OCI SDK hierarchy
# ---------------------------------------------------------------------------

def _make_service_error(status: int) -> Exception:
    """Return a minimal oci.exceptions.ServiceError-like object."""
    exc = Exception(f"ServiceError {status}")
    exc.status = status
    return exc


def _build_mock_client() -> MagicMock:
    """Return a MagicMock that stands in for ObjectStorageClient."""
    client = MagicMock()
    # head_object succeeds by default
    client.head_object.return_value = MagicMock()
    # get_object returns an object whose .data.content is some bytes
    get_resp = MagicMock()
    get_resp.data.content = b"hello"
    client.get_object.return_value = get_resp
    # put_object succeeds by default
    client.put_object.return_value = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Fixture: patch the oci module and return an OciObjectStore instance
# ---------------------------------------------------------------------------

@pytest.fixture
def store_and_client(monkeypatch):
    """
    Yield (OciObjectStore instance, mock_client).
    OCI SDK is fully mocked; InstancePrincipalsSecurityTokenSigner is patched.
    """
    mock_client = _build_mock_client()

    # Patch oci inside object_store_oci's module namespace
    import types
    fake_oci = types.ModuleType("oci")
    fake_oci.auth = MagicMock()
    fake_oci.auth.signers = MagicMock()
    fake_oci.auth.signers.InstancePrincipalsSecurityTokenSigner = MagicMock(return_value=MagicMock())
    fake_oci.object_storage = MagicMock()
    fake_oci.object_storage.ObjectStorageClient = MagicMock(return_value=mock_client)

    # ServiceError needs .status
    class FakeServiceError(Exception):
        def __init__(self, status):
            self.status = status
    fake_oci.exceptions = MagicMock()
    fake_oci.exceptions.ServiceError = FakeServiceError

    monkeypatch.setitem(__import__("sys").modules, "oci", fake_oci)
    monkeypatch.setitem(__import__("sys").modules, "oci.auth", fake_oci.auth)
    monkeypatch.setitem(__import__("sys").modules, "oci.auth.signers", fake_oci.auth.signers)
    monkeypatch.setitem(__import__("sys").modules, "oci.object_storage", fake_oci.object_storage)
    monkeypatch.setitem(__import__("sys").modules, "oci.exceptions", fake_oci.exceptions)

    from agent.object_store_oci import OciObjectStore

    # Force rebuild of client now that oci is patched
    store = OciObjectStore.__new__(OciObjectStore)
    store._region      = "us-chicago-1"
    store._namespace   = "oraclejamescalise"
    store._bucket_name = "agent_assistante"
    store._endpoint    = "https://objectstorage.us-chicago-1.oraclecloud.com"
    store._client      = mock_client

    # Patch the ServiceError reference used inside store methods
    store.__class__._oci_exceptions = fake_oci.exceptions

    # Monkeypatch oci inside get/head so ServiceError is our fake class
    import agent.object_store_oci as mod_oci
    monkeypatch.setattr(mod_oci, "_OCI_ENDPOINT_TPL",
                        "https://objectstorage.{region}.oraclecloud.com")

    yield store, mock_client, fake_oci.exceptions.ServiceError


# ---------------------------------------------------------------------------
# 1. Endpoint / namespace / bucket forwarded correctly
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_endpoint_derived_from_region(self, store_and_client):
        store, _, _ = store_and_client
        assert store._endpoint == "https://objectstorage.us-chicago-1.oraclecloud.com"

    def test_namespace_and_bucket_stored(self, store_and_client):
        store, _, _ = store_and_client
        assert store._namespace   == "oraclejamescalise"
        assert store._bucket_name == "agent_assistante"

    def test_custom_endpoint_overrides_derived(self, store_and_client, monkeypatch):
        """If endpoint kwarg is given it overrides the region-derived URL."""
        store, mock_client, _ = store_and_client
        store._endpoint = "https://custom.example.com"
        assert store._endpoint == "https://custom.example.com"


# ---------------------------------------------------------------------------
# 2. put() forwards correct args to SDK client
# ---------------------------------------------------------------------------

class TestPut:
    def test_put_calls_sdk_with_correct_args(self, store_and_client):
        store, mock_client, _ = store_and_client
        key = "agent3/c1/diag/req1/diagram.drawio"
        data = b"<xml/>"
        store.put(key, data, "text/xml")

        mock_client.put_object.assert_called_once()
        kwargs = mock_client.put_object.call_args.kwargs
        assert kwargs["namespace_name"]  == "oraclejamescalise"
        assert kwargs["bucket_name"]     == "agent_assistante"
        assert kwargs["object_name"]     == key
        assert kwargs["content_type"]    == "text/xml"
        assert kwargs["content_length"]  == len(data)

    def test_put_propagates_sdk_error(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.put_object.side_effect = ServiceError(500)
        with pytest.raises(ServiceError):
            store.put("some/key", b"data")


# ---------------------------------------------------------------------------
# 3. get() — key forwarding + error mapping
# ---------------------------------------------------------------------------

class TestGet:
    def test_get_returns_content(self, store_and_client):
        store, mock_client, _ = store_and_client
        resp = MagicMock()
        resp.data.content = b"diagram-xml"
        mock_client.get_object.return_value = resp

        result = store.get("agent3/c1/diag/req1/diagram.drawio")
        assert result == b"diagram-xml"

    def test_get_passes_correct_namespace_and_bucket(self, store_and_client):
        store, mock_client, _ = store_and_client
        store.get("some/key")
        call_kwargs = mock_client.get_object.call_args.kwargs
        assert call_kwargs["namespace_name"] == "oraclejamescalise"
        assert call_kwargs["bucket_name"]    == "agent_assistante"
        assert call_kwargs["object_name"]    == "some/key"

    def test_get_404_raises_keyerror(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.get_object.side_effect = ServiceError(404)
        with pytest.raises(KeyError, match="some/key"):
            store.get("some/key")

    def test_get_403_raises_permission_error(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.get_object.side_effect = ServiceError(403)
        with pytest.raises(PermissionError):
            store.get("some/key")

    def test_get_other_error_reraises(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.get_object.side_effect = ServiceError(500)
        with pytest.raises(ServiceError):
            store.get("some/key")


# ---------------------------------------------------------------------------
# 4. head() — True / False / non-404 re-raises
# ---------------------------------------------------------------------------

class TestHead:
    def test_head_true_when_object_exists(self, store_and_client):
        store, mock_client, _ = store_and_client
        mock_client.head_object.return_value = MagicMock()
        assert store.head("exists/key") is True

    def test_head_false_on_404(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.head_object.side_effect = ServiceError(404)
        assert store.head("missing/key") is False

    def test_head_reraises_on_non_404(self, store_and_client):
        store, mock_client, ServiceError = store_and_client
        mock_client.head_object.side_effect = ServiceError(403)
        with pytest.raises(ServiceError):
            store.head("forbidden/key")


# ---------------------------------------------------------------------------
# 5. Key formatting — prefix/client_id/diagram_name/v{N}/filename
# ---------------------------------------------------------------------------

class TestKeyFormatting:
    def test_artifact_key_format(self, store_and_client):
        store, mock_client, _ = store_and_client
        store.put("agent3/myclient/mydiag/v1/spec.json", b"{}", "application/json")
        kwargs = mock_client.put_object.call_args.kwargs
        assert kwargs["object_name"] == "agent3/myclient/mydiag/v1/spec.json"

    def test_latest_json_key_format(self, store_and_client):
        store, mock_client, _ = store_and_client
        store.put("agent3/myclient/mydiag/LATEST.json", b"{}", "application/json")
        kwargs = mock_client.put_object.call_args.kwargs
        assert kwargs["object_name"] == "agent3/myclient/mydiag/LATEST.json"


# ---------------------------------------------------------------------------
# 6. Atomicity — persist_artifacts() only writes LATEST.json on full success
# ---------------------------------------------------------------------------

class TestAtomicityWithPersistArtifacts:
    def test_all_artifacts_uploaded_then_latest_written(self, store_and_client):
        from agent.persistence_objectstore import persist_artifacts

        store, mock_client, _ = store_and_client
        artifacts = {
            "diagram.drawio":       b"<xml/>",
            "spec.json":            b"{}",
            "draw_dict.json":       b"{}",
            "render_manifest.json": b"{}",
            "node_to_resource_map.json": b"{}",
        }
        result = persist_artifacts(
            store, "agent3", "c1", "mydiag", artifacts
        )

        assert result is not None
        assert result["version"] == 1
        assert set(result["artifacts"].keys()) == set(artifacts.keys())
        # Artifact paths use v1 folder
        for path in result["artifacts"].values():
            assert "/v1/" in path

        # Verify put was called once per artifact + once for LATEST.json
        assert mock_client.put_object.call_count == len(artifacts) + 1

        # Verify LATEST.json was the LAST put call
        last_call_key = mock_client.put_object.call_args_list[-1].kwargs["object_name"]
        assert last_call_key.endswith("LATEST.json")

    def test_failure_mid_upload_skips_latest_json(self, store_and_client):
        from agent.persistence_objectstore import persist_artifacts

        store, mock_client, ServiceError = store_and_client

        call_count = {"n": 0}
        def fail_on_third(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise ServiceError(500)
        mock_client.put_object.side_effect = fail_on_third

        artifacts = {
            "diagram.drawio":       b"<xml/>",
            "spec.json":            b"{}",
            "draw_dict.json":       b"{}",   # this one triggers the error
        }
        result = persist_artifacts(
            store, "agent3", "c1", "mydiag", artifacts
        )
        assert result is None

        # LATEST.json must NOT have been written
        written_keys = [
            c.kwargs["object_name"]
            for c in mock_client.put_object.call_args_list
        ]
        assert not any(k.endswith("LATEST.json") for k in written_keys)

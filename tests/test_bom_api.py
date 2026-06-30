from __future__ import annotations

import asyncio
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import pytest

import drawing_agent_server
from drawing_agent_server import app, require_user
from agent.bom_service import BomService
from agent.persistence_objectstore import InMemoryObjectStore
from server.services import bom_artifacts
from server.services.bom_artifacts import structured_bom_result_uses_default_sizing


def _setup() -> None:
    app.state.bom_service = BomService()


def test_bom_health_and_refresh_and_chat_flow() -> None:
    _setup()
    with TestClient(app, raise_server_exceptions=True) as client:
        health = client.get("/api/bom/health")
        assert health.status_code == 200
        assert health.json()["ready"] is False

        chat_unready = client.post(
            "/api/bom/chat",
            json={"message": "Generate BOM for 4 OCPU and 64 GB RAM"},
        )
        assert chat_unready.status_code == 200
        body = chat_unready.json()
        assert body["type"] == "normal"
        assert "not ready" in body["reply"].lower()

        refresh = client.post("/api/bom/refresh-data")
        assert refresh.status_code == 200
        refresh_body = refresh.json()
        assert refresh_body["ready"] is True
        assert refresh_body["pricing_sku_count"] > 0

        chat_ready = client.post(
            "/api/bom/chat",
            json={"message": "Generate BOM for 8 OCPU, 128 GB RAM, 1 TB block storage, with load balancer"},
        )
        assert chat_ready.status_code == 200
        ready_body = chat_ready.json()
        assert ready_body["type"] in {"final", "question"}
        assert "trace_id" in ready_body

        if ready_body["type"] == "final":
            payload = ready_body["bom_payload"]
            xlsx = client.post("/api/bom/generate-xlsx", json={"bom_payload": payload})
            assert xlsx.status_code == 200
            assert xlsx.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert len(xlsx.content) > 200


def test_bom_xlsx_metadata_includes_resolved_input_count() -> None:
    metadata = drawing_agent_server._bom_xlsx_metadata(
        "oci-bom-test.xlsx",
        "customers/acme/bom/xlsx/oci-bom-test.xlsx",
        {
            "bom_payload": {
                "resolved_inputs": [
                    {"question_id": "bom.compute.ocpu", "answer": "48 OCPU"},
                    {"question_id": "bom.compute.memory", "answer": "768 GB RAM"},
                ]
            }
        },
    )

    assert metadata["resolved_input_count"] == 2


def test_structured_bom_guard_checks_block_and_object_storage_independently() -> None:
    result = {
        "structured_inputs": {
            "compute": {"ocpu": 12},
            "memory": {"gb": 96},
            "storage": {"block_gb": 2048, "object_gb": 5120},
            "region": "us-phoenix-1",
        },
        "bom_payload": {
            "region": "us-phoenix-1",
            "line_items": [
                {"sku": "B97384", "description": "E5 OCPU", "category": "compute", "quantity": 12},
                {"sku": "B97385", "description": "E5 Memory", "category": "compute", "quantity": 96},
                {"sku": "B91961", "description": "Block Volume Storage", "category": "storage", "quantity": 2048},
                {"sku": "B91962", "description": "Block Volume Performance Units", "category": "storage", "quantity": 20480},
                {"sku": "B91628", "description": "Object Storage", "category": "storage", "quantity": 5120},
            ],
        },
    }

    assert structured_bom_result_uses_default_sizing(result) is False
    result["bom_payload"]["line_items"][-1]["quantity"] = 1024
    assert structured_bom_result_uses_default_sizing(result) is True


def test_bom_xlsx_and_metadata_roll_back_when_consistency_writeback_fails(monkeypatch) -> None:
    class FakeService:
        def generate_xlsx(self, payload):
            return b"xlsx"

    store = InMemoryObjectStore()
    result = {
        "tool_calls": [{
            "tool": "generate_bom",
            "result_data": {
                "type": "final",
                "consistency_report": {"verdict": "pass"},
                "bom_payload": {
                    "line_items": [{
                        "sku": "B97384", "description": "E5 OCPU", "quantity": 4,
                        "unit_price": 0.03,
                    }]
                },
            },
        }]
    }
    monkeypatch.setattr(
        bom_artifacts,
        "write_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("context write failed")),
    )

    with pytest.raises(RuntimeError, match="context write failed"):
        asyncio.run(bom_artifacts.persist_bom_xlsx_downloads(
            "acme",
            store,
            result,
            bom_service_factory=FakeService,
            logger=__import__("logging").getLogger("test"),
        ))

    assert store.list("customers/acme/bom/xlsx/") == []
    assert "xlsx_artifact_key" not in result["tool_calls"][0]["result_data"]


def test_bom_refresh_requires_admin_group_when_auth_enabled(monkeypatch) -> None:
    _setup()
    monkeypatch.setattr(drawing_agent_server, "AUTH_ENABLED", True)
    monkeypatch.setattr(drawing_agent_server, "OIDC_REQUIRED_GROUP", "admins")

    app.dependency_overrides[require_user] = lambda: {
        "email": "test@example.com",
        "name": "Test",
        "groups": ["readers"],
    }

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post("/api/bom/refresh-data")
            assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
        monkeypatch.setattr(drawing_agent_server, "AUTH_ENABLED", False)
        monkeypatch.setattr(drawing_agent_server, "OIDC_REQUIRED_GROUP", "")


def test_root_serves_no_store_headers(tmp_path) -> None:
    index_html = tmp_path / "index.html"
    index_html.write_text("<html></html>")
    with patch.object(drawing_agent_server, "_UI_INDEX", index_html):
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert resp.headers["cache-control"] == "no-store, no-cache, must-revalidate, max-age=0"
            assert resp.headers["pragma"] == "no-cache"
            assert resp.headers["expires"] == "0"
            assert resp.headers["x-app-version"] == drawing_agent_server.AGENT_VERSION


def test_login_uses_oci_identity_domain_authorize_endpoint(monkeypatch) -> None:
    issuer = "https://idcs-example.identity.oraclecloud.com"
    monkeypatch.setattr(drawing_agent_server, "AUTH_ENABLED", True)
    monkeypatch.setattr(drawing_agent_server, "OIDC_CLIENT_ID", "client-id")
    monkeypatch.setattr(drawing_agent_server, "OIDC_REDIRECT_URI", "https://app.example.com/oauth2/callback")
    monkeypatch.setattr(drawing_agent_server, "OIDC_SCOPE", "openid profile email")
    monkeypatch.setattr(
        drawing_agent_server,
        "OIDC_AUTHORIZATION_ENDPOINT",
        drawing_agent_server._join_oidc_url(issuer, "/oauth2/v1/authorize"),
    )

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/login", follow_redirects=False)
            assert resp.status_code == 307
            parsed = urlparse(resp.headers["location"])
            assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{issuer}/oauth2/v1/authorize"
            params = parse_qs(parsed.query)
            assert params["client_id"] == ["client-id"]
            assert params["redirect_uri"] == ["https://app.example.com/oauth2/callback"]
            assert params["response_type"] == ["code"]
            assert params["scope"] == ["openid profile email"]
            assert params["state"][0]
    finally:
        monkeypatch.setattr(drawing_agent_server, "AUTH_ENABLED", False)

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import drawing_agent_server
from drawing_agent_server import app, require_user
from agent.bom_service import BomService


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


def test_root_serves_no_store_headers() -> None:
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

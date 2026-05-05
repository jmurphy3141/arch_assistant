from __future__ import annotations

from fastapi.testclient import TestClient

from drawing_agent_server import app
from agent.persistence_objectstore import InMemoryObjectStore


def _setup_client():
    store = InMemoryObjectStore()
    app.state.object_store = store
    app.state.llm_runner = object()
    return store


def test_terraform_generate_and_download(monkeypatch):
    store = _setup_client()

    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        _ = (task, engagement_context, trace_id)
        assert name == "terraform"
        return {
            "status": "ok",
            "result": {
                "main.tf": 'resource "oci_core_vcn" "main" {}',
                "providers.tf": 'terraform { required_version = ">= 1.6.0" }',
            },
            "summary": "Terraform generation completed",
            "trace": {},
        }

    from agent import sub_agent_client
    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.post(
            "/api/terraform/generate",
            json={
                "customer_id": "acme",
                "customer_name": "ACME Corp",
                "prompt": "Generate terraform for core networking",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == 1
        assert "main.tf" in body["files"]

        latest = client.get("/api/terraform/acme/latest")
        assert latest.status_code == 200
        assert latest.json()["latest"]["version"] == 1

        dl = client.get("/api/terraform/acme/download/main.tf")
        assert dl.status_code == 200
        assert "oci_core_vcn" in dl.text

    app.state.object_store = None
    app.state.llm_runner = None
    _ = store


def test_terraform_generate_clarification(monkeypatch):
    _setup_client()

    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        _ = (task, engagement_context, trace_id)
        assert name == "terraform"
        return {
            "status": "needs_input",
            "result": {},
            "summary": "Terraform generation blocked at stage `review`.",
            "blocking_questions": ["Need VCN CIDR"],
        }

    from agent import sub_agent_client
    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

    with TestClient(app, raise_server_exceptions=True) as client:
        resp = client.post(
            "/api/terraform/generate",
            json={
                "customer_id": "beta",
                "customer_name": "Beta Labs",
                "prompt": "Generate terraform",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "need_clarification"
        assert "Need VCN CIDR" in body["blocking_questions"]

    app.state.object_store = None
    app.state.llm_runner = None

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drawing_agent_server import app, _build_artifact_manifest
from agent.persistence_objectstore import InMemoryObjectStore
from agent.document_store import save_conversation_turns, save_project_engagement


@pytest.fixture
def client():
    store = InMemoryObjectStore()
    app.state.object_store = store
    app.state.llm_runner = object()  # placeholder so orchestrator text runner init passes
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client, store
    app.state.object_store = None
    app.state.llm_runner = None


def test_chat_history_index_returns_paginated_items(client):
    test_client, store = client

    save_conversation_turns(
        store,
        "acme",
        [
            {
                "role": "user",
                "content": "Need terraform for acme",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "ACME Corp",
            }
        ],
    )
    save_conversation_turns(
        store,
        "beta",
        [
            {
                "role": "user",
                "content": "Need a diagram",
                "timestamp": "2026-04-17T10:00:00Z",
                "customer_name": "Beta Labs",
            }
        ],
    )

    resp = test_client.get("/api/chat/history?page=1&page_size=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "trace_id" in body
    assert len(body["items"]) == 1
    assert body["pagination"]["total"] == 2
    assert body["pagination"]["has_next"] is True
    assert body["items"][0]["customer_id"] == "acme"


def test_chat_history_index_supports_search(client):
    test_client, store = client

    save_conversation_turns(
        store,
        "acme",
        [
            {
                "role": "user",
                "content": "Need terraform for acme",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "ACME Corp",
            }
        ],
    )
    save_conversation_turns(
        store,
        "beta",
        [
            {
                "role": "user",
                "content": "Need a diagram",
                "timestamp": "2026-04-17T10:00:00Z",
                "customer_name": "Beta Labs",
            }
        ],
    )

    resp = test_client.get("/api/chat/history?search=beta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["customer_id"] == "beta"


def test_chat_history_index_includes_project_metadata(client):
    test_client, store = client

    save_project_engagement(
        store,
        customer_id="acme-discovery",
        customer_name="Discovery",
        project_name="ACME Corp",
    )
    save_conversation_turns(
        store,
        "acme-discovery",
        [
            {
                "role": "user",
                "content": "Need terraform",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "Discovery",
            }
        ],
    )

    resp = test_client.get("/api/chat/history?search=acme")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["customer_id"] == "acme-discovery"
    assert item["engagement_id"] == "acme-discovery"
    assert item["project_id"] == "acme-corp"
    assert item["project_name"] == "ACME Corp"


def test_chat_projects_groups_multiple_engagements_by_project_name(client):
    test_client, store = client

    for customer_id, message, ts in [
        ("acme-discovery", "Discovery notes", "2026-04-17T10:00:00Z"),
        ("acme-build", "Build plan", "2026-04-17T13:00:00Z"),
    ]:
        save_project_engagement(
            store,
            customer_id=customer_id,
            customer_name=customer_id,
            project_name="ACME Corp",
        )
        save_conversation_turns(
            store,
            customer_id,
            [
                {
                    "role": "user",
                    "content": message,
                    "timestamp": ts,
                    "customer_name": customer_id,
                }
            ],
        )

    resp = test_client.get("/api/chat/projects")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    project = body["items"][0]
    assert project["project_id"] == "acme-corp"
    assert project["project_name"] == "ACME Corp"
    assert project["engagement_count"] == 2
    assert [item["customer_id"] for item in project["engagements"]] == ["acme-build", "acme-discovery"]


def test_chat_projects_legacy_histories_group_by_customer_name(client):
    test_client, store = client

    save_conversation_turns(
        store,
        "legacy-one",
        [
            {
                "role": "user",
                "content": "First legacy thread",
                "timestamp": "2026-04-17T10:00:00Z",
                "customer_name": "Legacy Co",
            }
        ],
    )
    save_conversation_turns(
        store,
        "legacy-two",
        [
            {
                "role": "user",
                "content": "Second legacy thread",
                "timestamp": "2026-04-17T11:00:00Z",
                "customer_name": "Legacy Co",
            }
        ],
    )

    resp = test_client.get("/api/chat/projects")
    assert resp.status_code == 200
    project = resp.json()["items"][0]
    assert project["project_id"] == "legacy-co"
    assert project["project_name"] == "Legacy Co"
    assert project["engagement_count"] == 2


def test_chat_projects_search_matches_engagement_and_last_message(client):
    test_client, store = client

    save_project_engagement(store, customer_id="acme-build", customer_name="Build", project_name="ACME Corp")
    save_conversation_turns(
        store,
        "acme-build",
        [
            {
                "role": "user",
                "content": "Need GPU sizing",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "Build",
            }
        ],
    )

    resp = test_client.get("/api/chat/projects?search=gpu")
    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 1

    resp = test_client.get("/api/chat/projects?search=acme-build")
    assert resp.status_code == 200
    assert resp.json()["pagination"]["total"] == 1


def test_chat_history_index_terraform_needs_input_status(client):
    test_client, store = client

    save_conversation_turns(
        store,
        "gamma",
        [
            {
                "role": "user",
                "content": "Please generate terraform",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "Gamma LLC",
            },
            {
                "role": "tool",
                "tool": "generate_terraform",
                "result_summary": "Terraform generation blocked at stage `review`. Clarifications required:\n- Need CIDR",
                "timestamp": "2026-04-17T13:01:00Z",
            },
        ],
    )

    resp = test_client.get("/api/chat/history?search=gamma")
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["status"] == "Terraform Needs Input"


def test_chat_stream_sse_mode(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Hello from stream",
            "tool_calls": [{"tool": "generate_pov", "args": {}, "result_summary": "ok"}],
            "artifacts": {},
            "history_length": 4,
        }

    import agent.orchestrator_agent as orchestrator_agent

    monkeypatch.setattr(orchestrator_agent, "run_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=sse",
        json={
            "customer_id": "acme",
            "customer_name": "ACME Corp",
            "message": "hi",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in resp.text
    assert "event: tool" in resp.text
    assert "event: token" in resp.text
    assert "event: completion" in resp.text
    assert '"reply": "Hello from stream"' in resp.text


def test_api_chat_includes_artifact_manifest(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Done",
            "tool_calls": [
                {
                    "tool": "generate_terraform",
                    "args": {},
                    "result_summary": "ok",
                    "result_data": {
                        "ok": True,
                        "bundle": {
                            "version": 1,
                            "files": {
                                "main.tf": "terraform/acme/v1/main.tf",
                                "providers.tf": "terraform/acme/v1/providers.tf",
                            },
                        },
                    },
                }
            ],
            "artifacts": {"generate_diagram": "agent3/acme/arch/v1/diagram.drawio"},
            "history_length": 3,
        }

    import drawing_agent_server as srv

    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat",
        json={"customer_id": "acme", "customer_name": "ACME Corp", "message": "go"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "artifact_manifest" in body
    downloads = body["artifact_manifest"]["downloads"]
    assert any(item["type"] == "diagram" for item in downloads)
    assert any(item["type"] == "terraform" and item["filename"] == "main.tf" for item in downloads)


def test_artifact_manifest_includes_multiple_diagram_tool_call_keys():
    manifest = _build_artifact_manifest(
        "acme",
        {
            "tool_calls": [
                {
                    "tool": "generate_diagram",
                    "scenario_label": "Scenario 1",
                    "artifact_key": "agent3/acme/lift-shift/v1/diagram.drawio",
                },
                {
                    "tool": "generate_diagram",
                    "scenario_label": "Scenario 2",
                    "artifact_key": "agent3/acme/oci-native/v1/diagram.drawio",
                },
            ],
            "artifacts": {"generate_diagram": "agent3/acme/oci-native/v1/diagram.drawio"},
        },
    )

    downloads = [item for item in manifest["downloads"] if item["type"] == "diagram"]
    assert [item["key"] for item in downloads] == [
        "agent3/acme/lift-shift/v1/diagram.drawio",
        "agent3/acme/oci-native/v1/diagram.drawio",
    ]
    assert [item["label"] for item in downloads] == ["Scenario 1", "Scenario 2"]


def test_chat_stream_chunked_mode(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Chunked output",
            "tool_calls": [{"tool": "generate_jep", "args": {}, "result_summary": "ok"}],
            "artifacts": {},
            "history_length": 7,
        }

    import agent.orchestrator_agent as orchestrator_agent

    monkeypatch.setattr(orchestrator_agent, "run_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=chunked",
        json={
            "customer_id": "beta",
            "customer_name": "Beta Labs",
            "message": "hello",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert '"event_type": "status"' in resp.text
    assert '"event_type": "tool"' in resp.text
    assert '"event_type": "token"' in resp.text
    assert '"event_type": "completion"' in resp.text
    assert '"reply": "Chunked output"' in resp.text


def test_chat_history_index_page_out_of_range(client):
    test_client, store = client

    save_conversation_turns(
        store,
        "acme",
        [
            {
                "role": "user",
                "content": "Need terraform",
                "timestamp": "2026-04-17T13:00:00Z",
                "customer_name": "ACME Corp",
            }
        ],
    )

    resp = test_client.get("/api/chat/history?page=3&page_size=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["items"] == []
    assert body["pagination"]["total"] == 1
    assert body["pagination"]["has_next"] is False


def test_chat_stream_sse_emits_error_event(monkeypatch, client):
    test_client, _store = client

    async def _broken_run_turn(**_kwargs):
        raise RuntimeError("orchestrator exploded")

    import drawing_agent_server as srv

    monkeypatch.setattr(srv, "_run_orchestrator_turn", _broken_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=sse",
        json={
            "customer_id": "err",
            "customer_name": "Err Corp",
            "message": "hi",
        },
    )
    assert resp.status_code == 200
    assert "event: error" in resp.text
    assert '"event_type": "error"' in resp.text
    assert "orchestrator exploded" in resp.text


def test_chat_stream_emits_terraform_stage_events(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Terraform done",
            "tool_calls": [
                {
                    "tool": "generate_terraform",
                    "args": {},
                    "result_summary": "Terraform generation completed",
                    "result_data": {
                        "ok": True,
                        "stages": [
                            {"stage": "plan-eng-review", "ok": True, "questions": [], "output_preview": "plan"},
                            {"stage": "review", "ok": True, "questions": [], "output_preview": "review"},
                        ],
                        "blocking_questions": [],
                    },
                }
            ],
            "artifacts": {},
            "history_length": 9,
        }

    import drawing_agent_server as srv

    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=sse",
        json={
            "customer_id": "tf",
            "customer_name": "TF Corp",
            "message": "generate terraform",
        },
    )
    assert resp.status_code == 200
    assert "event: terraform_stage" in resp.text
    assert '"event_type": "terraform_stage"' in resp.text
    assert '"stage": "plan-eng-review"' in resp.text


def test_chat_stream_completion_includes_artifact_manifest(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Done",
            "tool_calls": [
                {
                    "tool": "generate_terraform",
                    "args": {},
                    "result_summary": "ok",
                    "result_data": {
                        "ok": True,
                        "bundle": {
                            "version": 1,
                            "files": {
                                "main.tf": "terraform/acme/v1/main.tf",
                            },
                        },
                    },
                }
            ],
            "artifacts": {"generate_diagram": "agent3/acme/arch/v1/diagram.drawio"},
            "history_length": 9,
        }

    import drawing_agent_server as srv

    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=chunked",
        json={
            "customer_id": "acme",
            "customer_name": "ACME Corp",
            "message": "generate",
        },
    )
    assert resp.status_code == 200
    assert '"artifact_manifest"' in resp.text
    assert '/api/terraform/acme/download/main.tf' in resp.text

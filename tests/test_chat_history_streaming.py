from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drawing_agent_server import app, _build_artifact_manifest
from agent.persistence_objectstore import InMemoryObjectStore
from agent.context_store import read_context, write_context
from agent.document_store import (
    list_notes,
    load_conversation_history,
    load_conversation_summary,
    save_conversation_summary,
    save_conversation_turns,
    save_note,
    save_project_engagement,
)


class _FakeBomService:
    def __init__(self, content: bytes = b"fake-xlsx-content") -> None:
        self.content = content
        self.payloads: list[dict] = []

    def generate_xlsx(self, payload: dict) -> bytes:
        self.payloads.append(dict(payload))
        return self.content


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


def test_reset_chat_context_clears_active_memory_without_deleting_artifacts(client):
    test_client, store = client
    customer_id = "rga"

    context = read_context(store, customer_id, "RGA")
    context.update(
        {
            "agents": {
                "diagram": {
                    "notes_incorporated": ["notes/rga/discovery.md"],
                    "diagram_key": "customers/rga/diagrams/v1/diagram.drawio",
                }
            },
            "archie": {
                "engagement_summary": "Old operating model notes",
                "pending_update": {"status": "waiting"},
            },
            "latest_decision_context": {"goal": "export operating model"},
            "decision_log": [{"path": "export"}],
            "pending_checkpoint": {"id": "cp-1"},
        }
    )
    write_context(store, customer_id, context)
    save_conversation_turns(
        store,
        customer_id,
        [{"role": "user", "content": "old prompt", "timestamp": "2026-04-29T00:00:00Z"}],
    )
    save_conversation_summary(store, customer_id, "Older chat summary")
    raw_note_key = save_note(store, customer_id, "discovery.md", b"old active note")
    raw_artifact_key = "customers/rga/diagrams/v1/diagram.drawio"
    store.put(raw_artifact_key, b"<mxfile />", "application/xml")

    resp = test_client.post(f"/api/chat/{customer_id}/reset-context")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["customer_id"] == customer_id
    assert body["message"] == "Context reset."

    reset = read_context(store, customer_id)
    assert reset["agents"] == {}
    assert reset["archie"]["engagement_summary"] == ""
    assert reset["archie"]["pending_update"] is None
    assert reset["latest_decision_context"] == {}
    assert reset["decision_log"] == []
    assert reset["pending_checkpoint"] is None
    assert load_conversation_history(store, customer_id, max_turns=0) == []
    assert load_conversation_summary(store, customer_id) == ""
    assert list_notes(store, customer_id) == []
    assert store.head(raw_note_key)
    assert store.head(f"customers/{customer_id}/notes/discovery.md")
    assert store.head(raw_artifact_key)


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
    test_client, store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Done",
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "args": {},
                    "result_summary": "bom ok",
                    "result_data": {
                        "type": "final",
                        "bom_payload": {
                            "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                            "totals": {"estimated_monthly_cost": 500},
                        },
                        "archie_expert_review": {"verdict": "pass"},
                    },
                },
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

    fake_bom_service = _FakeBomService()
    monkeypatch.setattr(srv.app.state, "bom_service", fake_bom_service, raising=False)
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
    bom_download = next(item for item in downloads if item["type"] == "bom")
    assert bom_download["filename"].endswith(".xlsx")
    assert store.head(bom_download["key"])
    assert any(item["type"] == "terraform" and item["filename"] == "main.tf" for item in downloads)
    assert body["tool_calls"][0]["result_data"]["xlsx_artifact_key"] == bom_download["key"]

    download_resp = test_client.get(bom_download["download_url"])
    assert download_resp.status_code == 200
    assert download_resp.content == fake_bom_service.content
    assert download_resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_api_chat_does_not_persist_checkpointed_bom_xlsx(monkeypatch, client):
    test_client, store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "Cost checkpoint required.",
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "args": {},
                    "result_summary": "Final BOM prepared, checkpoint required.",
                    "result_data": {
                        "type": "final",
                        "bom_payload": {
                            "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                            "totals": {"estimated_monthly_cost": 7500},
                        },
                        "governor": {"overall_status": "checkpoint_required"},
                    },
                }
            ],
            "artifacts": {},
            "history_length": 1,
        }

    import drawing_agent_server as srv

    fake_bom_service = _FakeBomService()
    monkeypatch.setattr(srv.app.state, "bom_service", fake_bom_service, raising=False)
    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat",
        json={"customer_id": "checkpoint-bom", "customer_name": "Checkpoint BOM", "message": "build bom"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [item for item in body["artifact_manifest"]["downloads"] if item["type"] == "bom"] == []
    assert fake_bom_service.payloads == []
    assert not store.list("customers/checkpoint-bom/bom/xlsx/")


def test_api_chat_does_not_persist_empty_or_defaulted_structured_bom_xlsx(monkeypatch, client):
    test_client, store = client

    async def _fake_run_turn(**_kwargs):
        return {
            "reply": "BOM blocked.",
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "args": {},
                    "result_summary": "Final BOM prepared.",
                    "result_data": {
                        "type": "final",
                        "bom_payload": {"line_items": [], "totals": {}},
                        "archie_expert_review": {"verdict": "pass"},
                    },
                },
                {
                    "tool": "generate_bom",
                    "args": {},
                    "result_summary": "Final BOM prepared.",
                    "result_data": {
                        "type": "final",
                        "structured_inputs": {
                            "compute": {"ocpu": 64},
                            "memory": {"gb": 1146.88},
                            "storage": {"block_tb": 44},
                        },
                        "bom_payload": {
                            "line_items": [
                                {"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 4},
                                {"sku": "B94177", "description": "Compute memory", "category": "compute", "quantity": 64},
                                {"sku": "B91961", "description": "Block storage", "category": "storage", "quantity": 1024},
                            ],
                            "totals": {},
                        },
                        "archie_expert_review": {"verdict": "pass"},
                    },
                },
            ],
            "artifacts": {},
            "history_length": 1,
        }

    import drawing_agent_server as srv

    fake_bom_service = _FakeBomService()
    monkeypatch.setattr(srv.app.state, "bom_service", fake_bom_service, raising=False)
    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat",
        json={"customer_id": "structured-default-bom", "customer_name": "Structured BOM", "message": "build bom"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [item for item in body["artifact_manifest"]["downloads"] if item["type"] == "bom"] == []
    assert fake_bom_service.payloads == []
    assert not store.list("customers/structured-default-bom/bom/xlsx/")


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


def test_artifact_manifest_includes_bom_xlsx_download():
    metadata = {
        "schema_version": "1.0",
        "tool": "generate_bom",
        "status": "approved",
        "checkpoint_required": False,
    }
    manifest = _build_artifact_manifest(
        "acme",
        {
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "result_data": {
                        "type": "final",
                        "bom_payload": {
                            "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                        },
                        "xlsx_artifact_key": "customers/acme/bom/xlsx/oci-bom-test.xlsx",
                        "xlsx_filename": "oci-bom-test.xlsx",
                        "xlsx_metadata": metadata,
                    },
                },
                {
                    "tool": "generate_diagram",
                    "artifact_key": "agent3/acme/arch/v1/diagram.drawio",
                },
            ],
        },
    )

    downloads = manifest["downloads"]
    assert any(item["type"] == "diagram" for item in downloads)
    bom_download = next(item for item in downloads if item["type"] == "bom")
    assert bom_download["download_url"] == "/api/bom/acme/download/oci-bom-test.xlsx"


def test_artifact_manifest_hides_checkpointed_or_metadata_less_bom_xlsx():
    manifest = _build_artifact_manifest(
        "acme",
        {
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "result_data": {
                        "type": "final",
                        "xlsx_artifact_key": "customers/acme/bom/xlsx/old.xlsx",
                        "xlsx_filename": "old.xlsx",
                    },
                },
                {
                    "tool": "generate_bom",
                    "result_data": {
                        "type": "final",
                        "xlsx_artifact_key": "customers/acme/bom/xlsx/checkpoint.xlsx",
                        "xlsx_filename": "checkpoint.xlsx",
                        "xlsx_metadata": {
                            "tool": "generate_bom",
                            "status": "approved",
                            "checkpoint_required": False,
                        },
                        "governor": {"overall_status": "checkpoint_required"},
                    },
                },
            ],
        },
    )

    assert [item for item in manifest["downloads"] if item["type"] == "bom"] == []


def test_artifact_manifest_hides_failed_review_bom_xlsx():
    manifest = _build_artifact_manifest(
        "acme",
        {
            "tool_calls": [
                {
                    "tool": "generate_bom",
                    "result_data": {
                        "type": "final",
                        "xlsx_artifact_key": "customers/acme/bom/xlsx/failed.xlsx",
                        "xlsx_filename": "failed.xlsx",
                        "xlsx_metadata": {
                            "tool": "generate_bom",
                            "status": "approved",
                            "checkpoint_required": False,
                            "archie_review_verdict": "blocked",
                        },
                        "trace": {
                            "review_verdict": "blocked",
                            "review_findings": ["BOM sizing mismatch for OCPU: requested 48, produced 4."],
                        },
                    },
                },
            ],
        },
    )

    assert [item for item in manifest["downloads"] if item["type"] == "bom"] == []


def test_bom_xlsx_download_rejects_invalid_or_missing_filename(client):
    test_client, _store = client

    invalid = test_client.get("/api/bom/acme/download/not-a-workbook.txt")
    assert invalid.status_code == 400

    missing = test_client.get("/api/bom/acme/download/missing.xlsx")
    assert missing.status_code == 404


def test_bom_xlsx_download_rejects_metadata_less_file(client):
    test_client, store = client
    store.put(
        "customers/acme/bom/xlsx/old.xlsx",
        b"xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    resp = test_client.get("/api/bom/acme/download/old.xlsx")

    assert resp.status_code == 404


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


def test_chat_stream_emits_tool_started_status(monkeypatch, client):
    test_client, _store = client

    async def _fake_run_turn(req, **_kwargs):
        from agent.notifications import notify

        notify("tool_started:generate_bom", req.customer_id, "")
        await asyncio.sleep(0)
        return {
            "reply": "BOM done",
            "tool_calls": [{"tool": "generate_bom", "args": {}, "result_summary": "ok"}],
            "artifacts": {},
            "history_length": 1,
        }

    import drawing_agent_server as srv

    monkeypatch.setattr(srv, "_run_orchestrator_turn", _fake_run_turn)

    resp = test_client.post(
        "/api/chat/stream?mode=chunked",
        json={"customer_id": "stream-tool", "customer_name": "Stream Tool", "message": "build bom"},
    )

    assert resp.status_code == 200
    assert '"status": "tool_started"' in resp.text
    assert '"tool": "generate_bom"' in resp.text
    assert '"hat": "BOM"' in resp.text
    assert "Archie put on the BOM hat and is calling the BOM specialist." in resp.text


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

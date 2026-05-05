from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

import drawing_agent_server as srv
import agent.archie_loop as archie_loop
from agent.persistence_objectstore import InMemoryObjectStore

pytestmark = pytest.mark.system


@pytest.fixture
def client(monkeypatch):
    store = InMemoryObjectStore()
    srv.app.state.object_store = store
    srv.app.state.llm_runner = object()
    srv.app.state.persistence_config = {}

    responses = iter([
        '{"tool": "get_summary", "args": {}}',
        '{"tool": "generate_pov", "args": {}}',
        "POV generated and saved.",
    ])

    def fake_text_runner(_prompt: str, _system: str) -> str:
        return next(responses)

    async def fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        if tool_name == "get_summary":
            return "Summary: notes uploaded=true", "", {"context_version": 1}
        if tool_name == "generate_pov":
            return "POV v1 saved. Key: pov/test/v1.md", "pov/test/v1.md", {"version": 1}
        return "Unknown", "", {}

    monkeypatch.setattr(srv, "_make_orchestrator_text_runner", lambda: fake_text_runner)

    monkeypatch.setattr(
        archie_loop,
        "_parse_tool_call",
        lambda raw: json.loads(raw) if raw.strip().startswith("{") else None,
    )
    monkeypatch.setattr(archie_loop, "_execute_tool", fake_execute_tool)
    monkeypatch.setattr(archie_loop, "_engagement_context_supports_documents", lambda **_kwargs: True)

    with TestClient(srv.app, raise_server_exceptions=True) as tc:
        yield tc, store

    srv.app.state.object_store = None
    srv.app.state.llm_runner = None


def test_notes_to_orchestrator_to_artifacts_and_history(client):
    tc, store = client

    upload = tc.post(
        "/api/notes/upload",
        files={"file": ("note.md", io.BytesIO(b"Customer wants POV and timeline"), "text/markdown")},
        data={"customer_id": "sys001", "note_name": "note.md"},
    )
    assert upload.status_code == 200

    chat = tc.post(
        "/api/chat",
        json={
            "customer_id": "sys001",
            "customer_name": "System Test Customer",
            "message": "Generate POV from latest notes",
        },
    )
    assert chat.status_code == 200

    body = chat.json()
    assert body["status"] == "ok"
    assert "POV v1 saved. Key: pov/test/v1.md" in body["reply"]
    assert "Management Summary" in body["reply"]
    assert [c["tool"] for c in body["tool_calls"]] == ["generate_pov"]

    manifest = body.get("artifact_manifest", {})
    assert isinstance(manifest.get("downloads", []), list)

    history = tc.get("/api/chat/sys001/history")
    assert history.status_code == 200
    hbody = history.json()
    turns = hbody["history"]
    assert any(t.get("role") == "tool" and t.get("tool") == "generate_pov" for t in turns)

    # Verify notes and conversation were both persisted to the shared store.
    assert any(k.startswith("notes/sys001/") for k in store.list_keys())
    assert any(k.startswith("conversations/sys001/") for k in store.list_keys())

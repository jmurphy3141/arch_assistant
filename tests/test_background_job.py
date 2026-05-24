from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import agent.archie_session as archie_session
import agent.notifications as notifications
import agent.orchestrator_agent as orchestrator_agent
import drawing_agent_server
from agent.persistence_objectstore import InMemoryObjectStore


@pytest.fixture
def client(monkeypatch):
    drawing_agent_server._JOB_STORE.clear()
    drawing_agent_server.app.state.object_store = InMemoryObjectStore()
    drawing_agent_server.app.state.llm_runner = object()

    async def fake_run_turn(**_kwargs):
        return {
            "reply": "Background POC step complete.",
            "tool_calls": [],
            "artifacts": {},
            "history_length": 2,
            "events": [],
        }

    monkeypatch.setattr(archie_session, "run_turn", fake_run_turn)
    monkeypatch.setattr(orchestrator_agent, "run_turn", fake_run_turn)
    monkeypatch.setattr(notifications, "_load_telegram_config", lambda: {"enabled": False})

    with TestClient(drawing_agent_server.app, raise_server_exceptions=True) as test_client:
        yield test_client

    drawing_agent_server._JOB_STORE.clear()
    drawing_agent_server.app.state.object_store = None
    drawing_agent_server.app.state.llm_runner = None


def _start_background_job(client: TestClient) -> str:
    resp = client.post(
        "/api/chat/background",
        json={
            "customer_id": "acme",
            "customer_name": "Acme",
            "message": "Generate the POC artifacts in the background.",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["job_id"]
    return body["job_id"]


def _poll_job(client: TestClient, job_id: str) -> dict:
    for _ in range(20):
        resp = client.get(f"/api/job/{job_id}")
        if resp.status_code == 200:
            body = resp.json()
            if body.get("status") != "pending":
                return body
        time.sleep(0.05)
    raise AssertionError("Background job did not complete")


def test_background_endpoint_returns_202_with_job_id(client):
    _start_background_job(client)


def test_background_job_transitions_pending_to_complete(client):
    job_id = _start_background_job(client)
    body = _poll_job(client, job_id)
    assert body["status"] == "ok"
    assert body["reply"] == "Background POC step complete."


@pytest.mark.asyncio
async def test_telegram_notify_fires_when_enabled(monkeypatch):
    posts: list[tuple[str, dict]] = []

    class FakeAsyncClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict):
            posts.append((url, json))

    monkeypatch.setattr(notifications, "_load_telegram_config", lambda: {
        "enabled": True,
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
    })
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setattr(notifications.httpx, "AsyncClient", FakeAsyncClient)

    notifications.notify("poc_step_complete", "acme", "Done")
    await asyncio.sleep(0.01)

    assert posts == [
        (
            "https://api.telegram.org/bottoken-123/sendMessage",
            {
                "chat_id": "chat-456",
                "text": "*Archie*: `poc_step_complete`\n*Customer*: `acme`\nDone",
                "parse_mode": "Markdown",
            },
        )
    ]


def test_telegram_failure_does_not_fail_job(client, monkeypatch):
    class FailingAsyncClient:
        def __init__(self, timeout: float):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(notifications, "_load_telegram_config", lambda: {
        "enabled": True,
        "bot_token_env": "TELEGRAM_BOT_TOKEN",
        "chat_id_env": "TELEGRAM_CHAT_ID",
    })
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-456")
    monkeypatch.setattr(notifications.httpx, "AsyncClient", FailingAsyncClient)

    job_id = _start_background_job(client)
    body = _poll_job(client, job_id)

    assert body["status"] == "ok"
    assert body["reply"] == "Background POC step complete."

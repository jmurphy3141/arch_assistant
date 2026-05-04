from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent import sub_agent_client
from agent.sub_agent_client import SubAgentError, call_sub_agent, get_agent_card


def _write_config(tmp_path, base_url: str = "http://sub-agent.test") -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "sub_agents:\n"
        f'  bom: "{base_url}"\n',
        encoding="utf-8",
    )
    sub_agent_client._CONFIG_PATH = config_path
    sub_agent_client._CARD_CACHE.clear()


def _card() -> dict:
    return {
        "name": "bom",
        "description": "Produces a priced OCI Bill of Materials.",
        "inputs": {
            "required": ["task"],
            "optional": ["engagement_context", "trace_id"],
        },
        "output": "BOM JSON",
        "llm_model_id": "model",
    }


def _use_transport(monkeypatch, transport: httpx.MockTransport) -> None:
    original_async_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(sub_agent_client.httpx, "AsyncClient", _client)


def test_call_sub_agent_sends_correct_payload(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    seen_payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a2a/card":
            return httpx.Response(200, json=_card())
        seen_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={"result": {"ok": True}, "status": "ok", "trace": {"id": "t-1"}},
        )

    _use_transport(monkeypatch, httpx.MockTransport(handler))

    result = asyncio.run(
        call_sub_agent(
            "bom",
            "build bom",
            {"region": "us-ashburn-1"},
            "trace-123",
        )
    )

    assert result == {"result": {"ok": True}, "status": "ok", "trace": {"id": "t-1"}}
    assert len(seen_payloads) == 1
    assert seen_payloads[0] == {
        "task": "build bom",
        "engagement_context": {"region": "us-ashburn-1"},
        "trace_id": "trace-123",
    }


def test_get_agent_card_caches_after_first_call(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_card())

    _use_transport(monkeypatch, httpx.MockTransport(handler))

    first = asyncio.run(get_agent_card("bom"))
    second = asyncio.run(get_agent_card("bom"))

    assert first == second == _card()
    assert calls == 1


def test_sub_agent_error_raised_on_http_500(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a2a/card":
            return httpx.Response(200, json=_card())
        return httpx.Response(500, json={"status": "error"})

    _use_transport(monkeypatch, httpx.MockTransport(handler))

    with pytest.raises(SubAgentError):
        asyncio.run(call_sub_agent("bom", "build bom", {}, "trace-123"))


def test_needs_input_response_returned_without_raising(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a2a/card":
            return httpx.Response(200, json=_card())
        return httpx.Response(
            200,
            json={
                "result": "Which region should I use?",
                "status": "needs_input",
                "trace": {"agent": "bom"},
            },
        )

    _use_transport(monkeypatch, httpx.MockTransport(handler))

    result = asyncio.run(call_sub_agent("bom", "build bom", {}, "trace-123"))

    assert result == {
        "result": "Which region should I use?",
        "status": "needs_input",
        "trace": {"agent": "bom"},
    }

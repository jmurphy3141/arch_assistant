from __future__ import annotations

import asyncio

from drawing_agent_server import _run_orchestrator_turn, OrchestratorChatRequest
from agent.persistence_objectstore import InMemoryObjectStore
import agent.orchestrator_agent as orchestrator_agent


def _dummy_text_runner(prompt: str, system_message: str) -> str:
    _ = (prompt, system_message)
    return '{"ok": false, "output": "", "questions": ["Need module boundaries."]}'


def test_execute_tool_routes_to_langgraph_specialists(monkeypatch):
    called = {"count": 0}

    async def _fake_execute_tool(*args, **kwargs):
        called["count"] += 1
        return ("adapter-result", "artifact-key")

    import agent.langgraph_specialists as langgraph_specialists

    monkeypatch.setattr(langgraph_specialists, "execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="langgraph",
        )
    )

    assert called["count"] == 1
    assert result == ("adapter-result", "artifact-key", {})


def test_run_orchestrator_turn_passes_specialist_mode(monkeypatch):
    captured = {}

    async def _fake_run_turn(**kwargs):
        captured.update(kwargs)
        return {
            "reply": "ok",
            "tool_calls": [],
            "artifacts": {},
            "history_length": 1,
        }

    monkeypatch.setattr(orchestrator_agent, "run_turn", _fake_run_turn)

    req = OrchestratorChatRequest(
        customer_id="beta",
        customer_name="Beta Labs",
        message="hello",
    )
    result = asyncio.run(
        _run_orchestrator_turn(
            req=req,
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            orch_cfg={
                "max_tool_iterations": 5,
                "langgraph_enabled": False,
                "specialists_langgraph_enabled": True,
            },
        )
    )

    assert result["reply"] == "ok"
    assert captured["specialist_mode"] == "langgraph"


def test_generate_terraform_langgraph_mode_returns_blocking_questions():
    result = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_terraform",
            {"prompt": "Generate terraform for a secure VCN and OKE cluster."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="langgraph",
        )
    )

    assert "Terraform generation blocked at stage" in result[0]
    assert "Clarifications required" in result[0]
    assert isinstance(result[2], dict)
    assert "stages" in result[2]


def test_run_orchestrator_turn_falls_back_to_legacy_on_langgraph_error(monkeypatch):
    import agent.langgraph_orchestrator as langgraph_orchestrator

    async def _broken_langgraph(**_kwargs):
        raise RuntimeError("langgraph failed")

    captured = {}

    async def _fake_legacy_run_turn(**kwargs):
        captured.update(kwargs)
        return {
            "reply": "legacy-fallback-ok",
            "tool_calls": [],
            "artifacts": {},
            "history_length": 1,
        }

    monkeypatch.setattr(langgraph_orchestrator, "run_turn", _broken_langgraph)
    monkeypatch.setattr(orchestrator_agent, "run_turn", _fake_legacy_run_turn)

    req = OrchestratorChatRequest(
        customer_id="acme",
        customer_name="ACME Corp",
        message="hello",
    )
    result = asyncio.run(
        _run_orchestrator_turn(
            req=req,
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            orch_cfg={
                "max_tool_iterations": 5,
                "langgraph_enabled": True,
                "specialists_langgraph_enabled": True,
            },
        )
    )

    assert result["reply"] == "legacy-fallback-ok"
    assert captured["specialist_mode"] == "legacy"

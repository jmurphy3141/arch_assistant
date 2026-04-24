from __future__ import annotations

import asyncio
import sys
import types

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.integration


def test_bom_parallel_fast_path_returns_tool_summary_without_llm_freewrite(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for BOM fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        assert tool_name == "generate_bom"
        return (
            "Final BOM prepared. Review line items, then export JSON or XLSX.",
            "",
            {"trace": {"type": "final"}},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="bom-fast",
            customer_name="BOM Fast",
            user_message="build a bom for an HA web app for under $5000",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Final BOM prepared. Review line items, then export JSON or XLSX."
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom"]
    assert llm_calls["count"] == 0


def test_parallel_pov_jep_fast_path_returns_deterministic_tool_summary(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for POV/JEP fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        if tool_name == "generate_pov":
            return ("POV v2 saved. Key: pov/acme/v2.md", "pov/acme/v2.md", {})
        if tool_name == "generate_jep":
            return ("JEP v3 saved. Key: jep/acme/v3.md", "jep/acme/v3.md", {})
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        orchestrator_agent,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes exist and milestones captured",
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="pov-jep-fast",
            customer_name="POV JEP Fast",
            user_message="Generate POV and JEP for the customer workshop",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"].startswith("Completed the requested outputs:")
    assert "`generate_pov`: POV v2 saved. Key: pov/acme/v2.md" in result["reply"]
    assert "`generate_jep`: JEP v3 saved. Key: jep/acme/v3.md" in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_pov", "generate_jep"]
    assert llm_calls["count"] == 0


def test_diagram_bom_fast_path_routes_without_llm(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for diagram fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        assert tool_name == "generate_diagram"
        assert "bom_text" in args
        return (
            "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
            "diagrams/acme/oci_architecture/v1/diagram.drawio",
            {},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="diagram-fast",
            customer_name="Diagram Fast",
            user_message=(
                "Build a diagram from this BOM and write a drawio file to the bucket.\n\n"
                "| Category | Component | Specs/Details | Quantity |\n"
                "|----------|-----------|---------------|----------|\n"
                "| Compute (App Servers) | Ampere A1 Flex | 4 OCPU ARM, 24GB RAM, 200GB Block Vol | 3 |"
            ),
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio"
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_diagram"]
    assert llm_calls["count"] == 0


def test_sparse_notes_bom_and_diagram_request_runs_both_and_merges_checkpoint(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for sparse notes fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        if tool_name == "generate_bom":
            return (
                "Final BOM prepared. Ballpark monthly estimate captured.",
                "",
                {
                    "governor": {"overall_status": "checkpoint_required"},
                    "checkpoint": {
                        "prompt": (
                            "Discovery checkpoint required before final acceptance.\n"
                            "- Tool: generate_bom\n"
                            "- Why: Best-effort BOM is assumption-heavy.\n"
                            "Assumptions applied:\n"
                            "- Region not specified; assume primary OCI region from current tenancy preference. (risk: medium)\n"
                            "- Availability target assumed at 99.9%. (risk: low)\n"
                            "- No explicit monthly budget cap provided. (risk: high)\n"
                            "- Reply `approve checkpoint` to accept this draft direction or revise the request and rerun."
                        )
                    },
                },
            )
        if tool_name == "generate_diagram":
            return (
                "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
                "diagrams/acme/oci_architecture/v1/diagram.drawio",
                {},
            )
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        orchestrator_agent,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes exist and milestones captured",
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="notes-fast",
            customer_name="Notes Fast",
            user_message=(
                "From these rough workshop notes, create a ballpark BOM and a draw.io architecture diagram "
                "for an OCI web application. Assume sensible defaults where details are missing."
            ),
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom", "generate_diagram"]
    assert "Completed the requested outputs:" in result["reply"]
    assert "`generate_bom`: Final BOM prepared. Ballpark monthly estimate captured." in result["reply"]
    assert (
        "`generate_diagram`: Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio"
        in result["reply"]
    )
    assert "Assumptions applied:" in result["reply"]
    assert "Discovery checkpoint required before final acceptance." in result["reply"]
    assert llm_calls["count"] == 0


def test_plain_bom_and_diagram_wording_still_triggers_parallel_fast_path(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for dual-output notes fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        if tool_name == "generate_bom":
            return ("Final BOM prepared. Ballpark estimate only.", "", {})
        if tool_name == "generate_diagram":
            return (
                "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
                "diagrams/acme/oci_architecture/v1/diagram.drawio",
                {},
            )
        raise AssertionError(f"unexpected tool {tool_name}")

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        orchestrator_agent,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes exist and milestones captured",
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="notes-fast-plain",
            customer_name="Notes Fast Plain",
            user_message=(
                "I only got a small set of notes from the client. Need a ballpark BOM and diagram "
                "with standard safe assumptions for OCI."
            ),
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom", "generate_diagram"]
    assert "Assumptions applied:" in result["reply"]
    assert llm_calls["count"] == 0


def test_diagram_request_forces_tool_when_llm_freewrites_mermaid(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "```mermaid\ngraph TD\nA[Internet] --> B[LB]\n```"

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        assert tool_name == "generate_diagram"
        assert "drawio" in args["bom_text"].lower()
        return (
            "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
            "diagrams/acme/oci_architecture/v1/diagram.drawio",
            {},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="diagram-mermaid-guard",
            customer_name="Diagram Mermaid Guard",
            user_message="I need drawio XML for this architecture diagram, not mermaid.",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio"
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_diagram"]
    assert llm_calls["count"] == 1


def test_call_generate_diagram_surfaces_clarification_questions(monkeypatch) -> None:
    class _FakeResponse:
        def json(self):
            return {
                "task_id": "task-123",
                "status": "need_clarification",
                "outputs": {
                    "questions": [
                        {
                            "id": "regions.count",
                            "question": "How many OCI regions should this cover?",
                            "blocking": True,
                        }
                    ]
                },
            }

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 180

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            assert url == "http://localhost:8080/api/a2a/task"
            assert json["skill"] == "generate_diagram"
            assert json["inputs"]["notes"] == "Need a drawio diagram."
            return _FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_FakeAsyncClient))

    summary, key, result_data = asyncio.run(
        orchestrator_agent._call_generate_diagram(
            {"bom_text": "Need a drawio diagram."},
            "clarify-customer",
            "http://localhost:8080",
        )
    )

    assert key == ""
    assert "clarification required" in summary.lower()
    assert "How many OCI regions should this cover?" in summary
    assert result_data["questions"][0]["id"] == "regions.count"


def test_call_generate_diagram_strips_injected_guidance_from_notes(monkeypatch) -> None:
    seen = {}

    class _FakeResponse:
        def json(self):
            return {
                "task_id": "task-456",
                "status": "ok",
                "outputs": {"object_key": "agent3/acme/arch/v1/diagram.drawio"},
            }

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 180

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            seen["url"] = url
            seen["payload"] = json
            return _FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_FakeAsyncClient))

    summary, key, result_data = asyncio.run(
        orchestrator_agent._call_generate_diagram(
            {
                "bom_text": (
                    "Need a rough diagram from meeting notes.\n\n"
                    "[Decision Context]\n{\"goal\": \"x\"}\n[End Decision Context]\n\n"
                    "[Injected Skill Guidance]\nload balancer database internet\n[End Skill Guidance]\n"
                ),
                "_decision_context": {
                    "goal": "Need a rough diagram from meeting notes.",
                    "constraints": {},
                    "assumptions": [{"id": "region_default", "statement": "single region", "reason": "default", "risk": "low"}],
                    "success_criteria": [],
                    "missing_inputs": [],
                    "requires_user_confirmation": False,
                },
            },
            "sanitize-customer",
            "http://localhost:8080",
        )
    )

    assert seen["url"] == "http://localhost:8080/api/a2a/task"
    assert seen["payload"]["inputs"]["notes"] == "Need a rough diagram from meeting notes."
    assert "Decision Context" not in seen["payload"]["inputs"]["notes"]
    assert "load balancer database internet" not in seen["payload"]["inputs"]["notes"]
    assert "Assumption mode requested" in seen["payload"]["inputs"]["context"]
    assert key == "agent3/acme/arch/v1/diagram.drawio"
    assert result_data == {}
    assert summary == "Diagram generated. Key: agent3/acme/arch/v1/diagram.drawio"

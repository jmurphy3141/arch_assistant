from __future__ import annotations

import asyncio
import sys
import types

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent import context_store
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

    assert "Final BOM prepared. Review line items, then export JSON or XLSX." in result["reply"]
    assert "Management Summary" in result["reply"]
    assert "Applied skills: oci_bom_expert, orchestrator" in result["reply"]
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
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "pov-jep-fast", "POV JEP Fast")
    context_store.set_archie_engagement_summary(
        ctx,
        "Retail customer modernizing an OCI web application on private OKE with WAF and Autonomous Database.",
    )
    context_store.write_context(store, "pov-jep-fast", ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="pov-jep-fast",
            customer_name="POV JEP Fast",
            user_message="Generate POV and JEP for the customer workshop",
            store=store,
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"].startswith("Completed the requested outputs:")
    assert "Customer POV draft: POV v2 saved. Key: pov/acme/v2.md" in result["reply"]
    assert "Joint execution plan: JEP v3 saved. Key: jep/acme/v3.md" in result["reply"]
    assert "Management Summary" in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_pov", "generate_jep"]
    assert llm_calls["count"] == 0


def test_diagram_bom_fast_path_routes_without_llm(monkeypatch) -> None:
    llm_calls = {"count": 0}

    def _text_runner(prompt: str, system_message: str) -> str:
        llm_calls["count"] += 1
        _ = (prompt, system_message)
        return "This should not be used for diagram fast-path."

    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        if tool_name == "generate_bom":
            assert "prompt" in args
            return ("Final BOM prepared. Review line items.", "", {"type": "final"})
        if tool_name == "generate_diagram":
            assert "bom_text" in args
            return (
                "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
                "diagrams/acme/oci_architecture/v1/diagram.drawio",
                {},
            )
        raise AssertionError(f"unexpected tool {tool_name}")

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

    assert "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio" in result["reply"]
    assert "Management Summary" in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom", "generate_diagram"]
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
                    "decision_context": {
                        "assumptions": [
                            {
                                "id": "region_default",
                                "statement": "Region not specified; assume primary OCI region from current tenancy preference.",
                                "risk": "medium",
                            },
                            {
                                "id": "availability_default",
                                "statement": "Availability target assumed at 99.9%.",
                                "risk": "low",
                            },
                        ],
                        "missing_inputs": ["preferred OCI region"],
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
    assert "I built the requested workflow in prerequisite order:" in result["reply"]
    assert "Bill of materials: Final BOM prepared. Ballpark monthly estimate captured." in result["reply"]
    assert (
        "Architecture diagram: Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio"
        in result["reply"]
    )
    assert "Assumptions applied:" in result["reply"]
    assert "Missing inputs to tighten the next pass:" in result["reply"]
    assert "Management Summary" in result["reply"]
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

    assert "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio" in result["reply"]
    assert "Management Summary" in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_diagram"]
    assert llm_calls["count"] == 0


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
    assert result_data["diagram_recovery_status"] == "needs_clarification"


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
    assert result_data["diagram_recovery_status"] == "none"
    assert result_data["backend_error_message"] == ""
    assert summary == "Diagram generated. Key: agent3/acme/arch/v1/diagram.drawio"


def test_call_generate_diagram_retries_with_assumptions_and_preserves_backend_error(monkeypatch) -> None:
    seen_payloads = []
    responses = iter(
        [
            {
                "task_id": "task-1",
                "status": "error",
                "error_message": "Need multi-region posture, replication approach, and region pair.",
            },
            {
                "task_id": "task-1-retry",
                "status": "ok",
                "outputs": {
                    "object_key": "agent3/acme/arch/v2/diagram.drawio",
                    "render_manifest": {"node_count": 9},
                },
            },
        ]
    )

    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _FakeAsyncClient:
        def __init__(self, timeout):
            assert timeout == 180

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            assert url == "http://localhost:8080/api/a2a/task"
            seen_payloads.append(json)
            return _FakeResponse(next(responses))

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_FakeAsyncClient))

    summary, key, result_data = asyncio.run(
        orchestrator_agent._call_generate_diagram(
            {
                "bom_text": "Generate a multi-region OKE SaaS diagram across two regions with replication.",
                "_decision_context": {
                    "goal": "Generate a multi-region OKE SaaS diagram.",
                    "constraints": {},
                    "assumptions": [],
                    "success_criteria": [],
                    "missing_inputs": [],
                    "requires_user_confirmation": False,
                },
            },
            "retry-customer",
            "http://localhost:8080",
        )
    )

    assert len(seen_payloads) == 2
    assert "bounded architect assumptions" in seen_payloads[1]["inputs"]["context"]
    assert key == "agent3/acme/arch/v2/diagram.drawio"
    assert summary == "Diagram generated. Key: agent3/acme/arch/v2/diagram.drawio"
    assert result_data["backend_error_message"] == "Need multi-region posture, replication approach, and region pair."
    assert result_data["diagram_recovery_status"] == "retried_with_assumptions"
    assert result_data["diagram_final_disposition"] == "completed_with_assumptions"
    assert result_data["recovery_attempt_count"] == 1
    assumptions = {item["id"]: item for item in result_data["assumptions_used"]}
    assert "diagram_multi_region_posture_default" in assumptions
    assert "diagram_region_pair_default" in assumptions
    assert "diagram_replication_default" in assumptions


def test_call_generate_diagram_turns_unrecoverable_error_into_actionable_reply(monkeypatch) -> None:
    class _FakeResponse:
        def json(self):
            return {
                "task_id": "task-2",
                "status": "error",
                "error_message": "Cross-region invariant violation: active-active with a single writable database is unsupported.",
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
            assert "active-active" in json["inputs"]["notes"].lower()
            return _FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_FakeAsyncClient))

    summary, key, result_data = asyncio.run(
        orchestrator_agent._call_generate_diagram(
            {
                "bom_text": "Generate an active-active multi-region OKE diagram with one writable database.",
            },
            "backend-error-customer",
            "http://localhost:8080",
        )
    )

    assert key == ""
    assert "backend layout invariant" in summary.lower()
    assert "single writable database is unsupported" in summary.lower()
    assert result_data["diagram_recovery_status"] == "backend_error"
    assert result_data["backend_error_message"].startswith("Cross-region invariant violation")


def test_call_generate_diagram_error_can_request_concrete_clarification(monkeypatch) -> None:
    class _FakeResponse:
        def json(self):
            return {
                "task_id": "task-clarify-after-error",
                "status": "error",
                "error_message": "Insufficient topology detail to build the diagram.",
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
            assert json["inputs"]["notes"] == "Need a diagram."
            return _FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=_FakeAsyncClient))

    summary, key, result_data = asyncio.run(
        orchestrator_agent._call_generate_diagram(
            {"bom_text": "Need a diagram."},
            "clarify-after-error",
            "http://localhost:8080",
        )
    )

    assert key == ""
    assert "Questions:" in summary
    assert "major OCI components" in summary
    assert result_data["diagram_recovery_status"] == "needs_clarification"
    assert result_data["questions"][0]["id"] == "workload.components"


def test_single_diagram_reply_includes_assumptions_from_result_data(monkeypatch) -> None:
    async def _fake_execute_tool(tool_name: str, args: dict, **_kwargs):
        _ = args
        assert tool_name == "generate_diagram"
        return (
            "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
            "diagrams/acme/oci_architecture/v1/diagram.drawio",
            {
                "diagram_recovery_status": "retried_with_assumptions",
                "diagram_final_disposition": "completed_with_assumptions",
                "assumptions_used": [
                    {
                        "id": "diagram_multi_region_posture_default",
                        "statement": "Multi-region posture not specified; assume active-passive HA/DR across two OCI regions.",
                        "reason": "missing posture",
                        "risk": "medium",
                    }
                ],
                "decision_context": {
                    "goal": "Generate a diagram.",
                    "constraints": {},
                    "assumptions": [],
                    "success_criteria": [],
                    "missing_inputs": [],
                    "requires_user_confirmation": False,
                },
            },
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="diagram-assumption-reply",
            customer_name="Diagram Assumption Reply",
            user_message="Generate a multi-region OKE diagram across two regions.",
            store=InMemoryObjectStore(),
            text_runner=lambda *_args: "Diagram response should not come from free text.",
            specialist_mode="legacy",
        )
    )

    assert "Assumptions applied:" in result["reply"]
    assert "active-passive ha/dr" in result["reply"].lower()


def test_synthesize_management_metadata_is_stable_and_complete() -> None:
    tool_calls = [
        {
            "tool": "generate_pov",
            "result_summary": "POV saved. Key: pov/acme/v2.md",
            "artifact_key": "pov/acme/v2.md",
            "result_data": {
                "applied_skills": ["oci_customer_pov_writer", "orchestrator"],
                "refinement_count": 2,
                "decision_context": {
                    "assumptions": [
                        {
                            "id": "region",
                            "statement": "Region not specified; assume us-ashburn-1.",
                            "risk": "low",
                        }
                    ]
                },
                "governor": {
                    "decision_summary": "Meets customer outcome framing.",
                    "security": {"findings": []},
                    "cost": {"findings": ["Budget range should be validated in discovery."]},
                    "quality": {"summary": "Quality fallback should not be used."},
                },
                "last_critique": {"critique_summary": "Critique fallback should not be used."},
                "decision_log": {"artifact_refs": ["pov/acme/v2.md"]},
            },
        },
        {
            "tool": "generate_jep",
            "result_summary": "JEP saved. Key: jep/acme/v1.md",
            "artifact_key": "jep/acme/v1.md",
            "result_data": {
                "applied_skills": ["oci_jep_writer", "orchestrator"],
                "governor": {
                    "quality": {
                        "summary": "Timeline is acceptable.",
                        "suggestions": ["Confirm owner names."],
                    }
                },
            },
        },
    ]

    first = orchestrator_agent._synthesize_management_metadata(tool_calls)
    second = orchestrator_agent._synthesize_management_metadata(tool_calls)

    assert first == second
    assert first["applied_skills"] == [
        "oci_customer_pov_writer",
        "orchestrator",
        "oci_jep_writer",
    ]
    assert first["refinement_count"] == 2
    assert first["governor_critic_summary"] == "Meets customer outcome framing.; Timeline is acceptable."
    assert first["key_tradeoffs"] == ["Budget range should be validated in discovery.", "Confirm owner names."]
    assert first["artifact_refs"] == ["pov/acme/v2.md", "jep/acme/v1.md"]
    assert first["checkpoint_status"] == "none"


def test_management_summary_renders_governor_summary_and_artifact_refs() -> None:
    summary = orchestrator_agent._render_management_summary(
        [
            {
                "tool": "generate_pov",
                "result_summary": "POV saved. Key: pov/acme/v2.md",
                "artifact_key": "pov/acme/v2.md",
                "result_data": {
                    "applied_skills": ["oci_customer_pov_writer", "orchestrator"],
                    "governor": {"quality": {"summary": "Quality review passed."}},
                },
            }
        ]
    )

    assert "Management Summary" in summary
    assert "- Governor/critic summary: Quality review passed." in summary
    assert "- Artifact refs: pov/acme/v2.md" in summary

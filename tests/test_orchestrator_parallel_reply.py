from __future__ import annotations

import asyncio
import sys
import types

import pytest

import agent.orchestrator_agent as orchestrator_agent
import agent.archie_session as archie_session
import agent.archie_memory as archie_memory
from agent import context_store
from agent.tools import specialists as specialists_module
from agent import sub_agent_client
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.integration


def test_simple_why_question_gets_clean_answer_without_mission_nudge() -> None:
    responses = iter(
        [
            (
                "## Recommendation\n"
                "- First point.\n"
                "- Second point.\n"
                "- Third point.\n"
                "- Fourth point."
            ),
            "OCI is the better fit here because the workload is Oracle Database-heavy and cost-sensitive.",
        ]
    )
    last = {"value": ""}

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        try:
            last["value"] = next(responses)
        except StopIteration:
            pass
        return last["value"]

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="why-clean-answer",
            customer_name="Why Clean Answer",
            user_message="Why would we pick OCI here?",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == (
        "OCI is the better fit here because the workload is Oracle Database-heavy and cost-sensitive."
    )
    assert "next required artifact" not in result["reply"]
    assert result["tool_calls"] == []


def test_active_poc_question_uses_persisted_context_without_poc_tool() -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "active-poc", "RGA")
    context_store.set_resolved_decisions(
        ctx,
        poc={
            "recommended_option": "AskRGA RAG + GenAI PoC",
            "success_criteria": "Answer telemetry questions with citations in under 5 seconds.",
        },
    )
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 4,
            "diagram_name": "askrga-rag-genai",
            "diagram_key": "diagrams/active-poc/askrga/v4/diagram.drawio",
            "deployment_summary": "RAG retrieval and GenAI response path",
        },
    )
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 5,
            "summary": "100GB repository and 1M tokens/day",
            "estimated_monthly_cost": 1234,
        },
    )
    context_store.write_context(store, "active-poc", ctx)
    archie_session.document_store.save_doc(
        store,
        "jep",
        "active-poc",
        """# Joint Execution Plan - AskRGA RAG + GenAI PoC

## Executive Summary
RGA will validate a RAG and GenAI assistant for telemetry questions using OCI services and governed external inference.
""",
        {"source": "test"},
    )

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        raise AssertionError("POC recall should not call the LLM")

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="active-poc",
            customer_name="RGA",
            user_message="what POC are we working on",
            store=store,
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["tool_calls"] == []
    assert "AskRGA RAG + GenAI PoC" in result["reply"]
    assert "diagrams/active-poc/askrga/v4/diagram.drawio" in result["reply"]
    assert "generate_poc_plan" not in result["reply"]


def test_active_poc_question_without_context_is_clear_without_tool_call() -> None:
    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        raise AssertionError("empty POC recall should not call the LLM")

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="active-poc-empty",
            customer_name="RGA",
            user_message="what POC are we working on",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            specialist_mode="legacy",
        )
    )

    assert result["tool_calls"] == []
    assert "verified POC" in result["reply"]
    assert "generate_poc_plan" not in result["reply"]


def test_legacy_jep_update_uses_prior_jep_and_artifact_context(monkeypatch) -> None:
    store = InMemoryObjectStore()
    archie_session.document_store.save_doc(
        store,
        "jep",
        "legacy-jep",
        "# Joint Execution Plan - AskRGA RAG + GenAI PoC\n\n## Executive Summary\nExisting base.",
        {"source": "test"},
    )
    ctx = context_store.read_context(store, "legacy-jep", "RGA")
    context_store.set_resolved_decisions(
        ctx,
        poc={"recommended_option": "AskRGA RAG + GenAI PoC"},
    )
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 4,
            "diagram_name": "askrga-rag-genai",
            "diagram_key": "diagrams/legacy-jep/askrga/v4/diagram.drawio",
        },
    )
    context_store.write_context(store, "legacy-jep", ctx)
    captured = {}

    async def _fake_call_sub_agent(name, task, engagement_context, trace_id):
        _ = trace_id
        assert name == "jep"
        captured["task"] = task
        captured["engagement_context"] = engagement_context
        return {
            "status": "ok",
            "result": "# Joint Execution Plan - AskRGA RAG + GenAI PoC\n\n## Executive Summary\nUpdated base.",
        }

    monkeypatch.setattr(archie_session.sub_agent_client, "call_sub_agent", _fake_call_sub_agent)
    monkeypatch.setattr(specialists_module, "_jep_writer_review_findings", lambda content: [])

    summary, key, data = asyncio.run(
        archie_session._legacy_tool_core_compat(
            "generate_jep",
            {"feedback": "Please update the JEP"},
            customer_id="legacy-jep",
            customer_name="RGA",
            store=store,
            text_runner=lambda prompt, system_message: "",
            a2a_base_url="http://localhost:8080",
        )
    )

    assert summary.startswith("JEP v2 saved.")
    assert key == "jep/legacy-jep/v2.md"
    assert data["docx_key"] == "jep/legacy-jep/v2.docx"
    assert "JEP REVISION GROUNDING" in captured["task"]
    assert "AskRGA RAG + GenAI PoC" in captured["task"]
    assert captured["engagement_context"]["artifact_context"]["diagram"]["diagram_name"] == "askrga-rag-genai"


def test_legacy_jep_review_failure_repairs_before_save(monkeypatch) -> None:
    store = InMemoryObjectStore()
    archie_session.document_store.save_doc(
        store,
        "jep",
        "legacy-jep-repair",
        "# Joint Execution Plan - AskRGA RAG + GenAI PoC\n\n## Executive Summary\nExisting base.",
        {"source": "test"},
    )
    calls: list[str] = []

    async def _fake_call_sub_agent(name, task, engagement_context, trace_id):
        _ = (engagement_context, trace_id)
        assert name == "jep"
        calls.append(task)
        if len(calls) == 1:
            return {"status": "ok", "result": "# Joint Execution Plan\n\n## Executive Summary\npartial"}
        assert "JEP REVIEW REPAIR" in task
        return {"status": "ok", "result": "# Joint Execution Plan\n\n## Executive Summary\nrepaired"}

    monkeypatch.setattr(archie_session.sub_agent_client, "call_sub_agent", _fake_call_sub_agent)
    monkeypatch.setattr(
        specialists_module,
        "_jep_writer_review_findings",
        lambda content: ["missing required sections"] if "partial" in content else [],
    )

    summary, key, data = asyncio.run(
        archie_session._legacy_tool_core_compat(
            "generate_jep",
            {"feedback": "Please update the JEP"},
            customer_id="legacy-jep-repair",
            customer_name="RGA",
            store=store,
            text_runner=lambda prompt, system_message: "",
            a2a_base_url="http://localhost:8080",
        )
    )

    assert summary.startswith("JEP v2 saved.")
    assert key == "jep/legacy-jep-repair/v2.md"
    assert data["repair_attempted"] is True
    assert data["initial_review_findings"] == ["missing required sections"]
    assert len(calls) == 2


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)

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
    assert "Management Summary" not in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom"]
    assert llm_calls["count"] >= 1


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
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

    assert result["reply"].startswith("I generated the requested outputs:")
    assert "POV: POV v2 saved. Key: pov/acme/v2.md" in result["reply"]
    assert "JEP: JEP v3 saved. Key: jep/acme/v3.md" in result["reply"]
    assert "Management Summary" not in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_pov", "generate_jep"]
    assert llm_calls["count"] >= 1


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)

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
    assert "Management Summary" not in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_bom", "generate_diagram"]
    assert llm_calls["count"] >= 1


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
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
    assert "I generated the requested outputs:" in result["reply"]
    assert "BOM: Final BOM prepared. Ballpark monthly estimate captured." in result["reply"]
    assert (
        "Diagram: Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio"
        in result["reply"]
    )
    assert "Assumptions applied:" in result["reply"]
    assert "Missing inputs to tighten the next pass:" in result["reply"]
    assert "Management Summary" not in result["reply"]
    assert llm_calls["count"] >= 1


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
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
    assert "I generated the requested outputs:" in result["reply"]
    assert llm_calls["count"] >= 1


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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)

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
    assert "Management Summary" not in result["reply"]
    assert [c["tool"] for c in result["tool_calls"]] == ["generate_diagram"]
    assert llm_calls["count"] >= 1


def test_call_generate_diagram_surfaces_clarification_questions(monkeypatch) -> None:
    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        assert name == "diagram"
        assert task.startswith("Need a drawio diagram.")
        return {
            "status": "needs_input",
            "result": '[{"id":"regions.count","question":"How many OCI regions should this cover?","blocking":true}]',
            "trace": {"trace_id": trace_id},
        }

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

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

    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        seen["name"] = name
        seen["task"] = task
        seen["engagement_context"] = engagement_context or {}
        seen["trace_id"] = trace_id
        return {"status": "ok", "result": "<mxfile />", "trace": {"trace_id": trace_id}}

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

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

    assert seen["name"] == "diagram"
    assert seen["task"].startswith("Need a rough diagram from meeting notes.")
    assert "Decision Context" not in seen["task"]
    assert "load balancer database internet" not in seen["task"]
    assert "Assumption mode requested" in seen["task"]
    assert key == ""
    assert result_data["diagram_recovery_status"] == "none"
    assert result_data["backend_error_message"] == ""
    assert summary.startswith("Diagram generated (task ")


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

    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        assert name == "diagram"
        seen_payloads.append({"task": task, "engagement_context": engagement_context or {}, "trace_id": trace_id})
        body = next(responses)
        if body["status"] == "ok":
            return {"status": "ok", "result": "<mxfile />", "trace": body["outputs"]}
        return {"status": "error", "result": body["error_message"], "trace": {}}

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

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
    assert "bounded architect assumptions" in seen_payloads[1]["task"]
    assert key == ""
    assert summary.startswith("Diagram generated (task ")
    assert result_data["backend_error_message"] == "Need multi-region posture, replication approach, and region pair."
    assert result_data["diagram_recovery_status"] == "retried_with_assumptions"
    assert result_data["diagram_final_disposition"] == "completed_with_assumptions"
    assert result_data["recovery_attempt_count"] == 1
    assumptions = {item["id"]: item for item in result_data["assumptions_used"]}
    assert "diagram_multi_region_posture_default" in assumptions
    assert "diagram_region_pair_default" in assumptions
    assert "diagram_replication_default" in assumptions


def test_call_generate_diagram_turns_unrecoverable_error_into_actionable_reply(monkeypatch) -> None:
    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        assert name == "diagram"
        assert "active-active" in task.lower()
        return {
            "status": "error",
            "result": "Cross-region invariant violation: active-active with a single writable database is unsupported.",
            "trace": {"trace_id": trace_id},
        }

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

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
    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        assert name == "diagram"
        assert task.startswith("Need a diagram.")
        return {
            "status": "error",
            "result": "Insufficient topology detail to build the diagram.",
            "trace": {"trace_id": trace_id},
        }

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

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

    monkeypatch.setattr(archie_session, "_execute_tool", _fake_execute_tool)

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

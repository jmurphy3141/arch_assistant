from __future__ import annotations

import asyncio
import time
from pathlib import Path

import drawing_agent_server as srv
from drawing_agent_server import _run_orchestrator_turn, OrchestratorChatRequest
from agent.bom_parser import ServiceItem
from agent import context_store
from agent import sub_agent_client
from agent.drawio_inspector import inspect_drawio_xml
from agent.persistence_objectstore import InMemoryObjectStore
import agent.orchestrator_agent as orchestrator_agent
import agent.archie_loop as archie_loop
import agent.archie_memory as archie_memory
from agent import skill_loader


REQUIRED_GSTACK_SKILLS = (
    "orchestrator",
    "orchestrator_critic",
    "oci_waf_reviewer",
    "oci_jep_writer",
    "oci_bom_expert",
    "diagram_for_oci",
    "oci_customer_pov_writer",
    "terraform_for_oci",
)


def _dummy_text_runner(prompt: str, system_message: str) -> str:
    _ = (prompt, system_message)
    return '{"ok": false, "output": "", "questions": ["Need module boundaries."]}'


def _seed_pov_context(store: InMemoryObjectStore, customer_id: str = "acme", customer_name: str = "ACME Corp") -> None:
    ctx = context_store.read_context(store, customer_id, customer_name)
    context_store.set_archie_engagement_summary(
        ctx,
        "Retail customer modernizing to private OKE with WAF and Autonomous Database.",
    )
    context_store.write_context(store, customer_id, ctx)


def test_execute_tool_routes_to_langgraph_specialists(monkeypatch):
    called = {"count": 0}

    async def _fake_execute_tool(*args, **kwargs):
        called["count"] += 1
        return ("adapter-result", "artifact-key")

    import agent.langgraph_specialists as langgraph_specialists

    monkeypatch.setattr(langgraph_specialists, "execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes captured for customer",
    )

    result = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "VCN + LB"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="langgraph",
        )
    )

    assert called["count"] == 1
    assert result[0] == "adapter-result"
    assert result[1] == "artifact-key"
    assert result[2].get("skill_preflight", {}).get("status") == "allow"
    assert result[2].get("skill_postflight", {}).get("status") == "allow"


def test_diagram_graph_uses_a2a_task_endpoint(monkeypatch):
    from agent.graphs import diagram_graph

    captured = {}

    async def _fake_call_sub_agent(name, task, engagement_context=None, trace_id=""):
        captured["name"] = name
        captured["task"] = task
        captured["engagement_context"] = engagement_context or {}
        captured["trace_id"] = trace_id
        return {
            "status": "ok",
            "result": "<mxfile />",
            "trace": {
                "render_manifest": {"node_count": 6},
                "node_to_resource_map": {
                    "oke_1": {"oci_type": "container engine", "layer": "compute", "label": "OKE"}
                },
                "draw_dict": {"boxes": [{"id": "app", "box_type": "_subnet_box", "tier": "app"}]},
                "spec": {"deployment_type": "single_ad"},
            },
        }

    monkeypatch.setattr(sub_agent_client, "call_sub_agent", _fake_call_sub_agent)

    summary, key, result_data = asyncio.run(
        diagram_graph.run(
            args={"bom_text": "Generate an OKE diagram", "_standards_bundle_version": "2026.04.24"},
            customer_id="acme",
            a2a_base_url="http://localhost:8080",
        )
    )

    assert captured["name"] == "diagram"
    assert captured["task"].startswith("Generate an OKE diagram")
    assert summary.startswith("Diagram generated (task ")
    assert key == ""
    assert result_data["drawio_xml"] == "<mxfile />"


def test_diagram_pipeline_applies_ocvs_bm_x9_fd_local_overlay(monkeypatch, tmp_path):
    generic_spec = {
        "deployment_type": "single_ad",
        "regions": [
            {
                "id": "region_box",
                "label": "OCI Region",
                "regional_subnets": [],
                "availability_domains": [
                    {
                        "id": "ad1_box",
                        "label": "Availability Domain 1",
                        "fault_domains": [
                            {"id": "fd1_box", "label": "FD1", "subnets": []},
                            {"id": "fd2_box", "label": "FD2", "subnets": []},
                            {"id": "fd3_box", "label": "FD3", "subnets": []},
                        ],
                        "subnets": [
                            {
                                "id": "public_subnet",
                                "label": "Public Subnet",
                                "tier": "ingress",
                                "nodes": [{"id": "compute_1", "type": "compute", "label": "Generic Compute"}],
                            },
                            {"id": "db_subnet", "label": "DB Subnet", "tier": "db", "nodes": []},
                        ],
                    }
                ],
                "gateways": [],
                "oci_services": [],
            }
        ],
        "external": [],
        "edges": [],
    }

    srv.app.state.llm_runner = lambda _prompt, _client_id: generic_spec
    srv.app.state.object_store = None
    srv.app.state.persistence_config = {}
    monkeypatch.setattr(srv, "OUTPUT_DIR", tmp_path)

    request_context = (
        "Target architecture is OCI Dedicated VMware Solution in af-johannesburg-1. "
        "Regenerate the diagram for VMware ESXi / VxRail with two BM.Standard.X9.64 hosts: "
        "host 1 in FD1 using FD-local subnet and host 2 in FD2 using FD-local subnet."
    )

    result = asyncio.run(
        srv.run_pipeline(
            items=[ServiceItem(id="compute_1", oci_type="compute", label="Compute - Standard - X9", layer="compute")],
            prompt="generic prompt",
            diagram_name="ocvs-bm-overlay",
            client_id="acme",
            request_id="req-ocvs-bm",
            input_hash="hash-ocvs-bm",
            reference_context_text=request_context,
        )
    )

    view = inspect_drawio_xml(result["drawio_xml"])
    labels = "\n".join(view["labels"])

    assert result["status"] == "ok"
    assert result["render_manifest"]["ocvs_bm_overlay_applied"] is True
    assert labels.count("BM.Standard.X9.64 ESXi Host") == 2
    assert "BM.Standard.X9.64 ESXi Host - FD1" in labels
    assert "BM.Standard.X9.64 ESXi Host - FD2" in labels
    assert "FD1 OCVS Management Subnet" in labels
    assert "FD1 ESXi Host Subnet" in labels
    assert "FD2 OCVS Management Subnet" in labels
    assert "FD2 ESXi Host Subnet" in labels
    assert any(marker in labels for marker in ("OCI Dedicated VMware Solution SDDC", "vCenter", "NSX"))
    assert "Public Subnet" not in labels
    assert "DB Subnet" not in labels


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
    assert captured["max_refinements"] == 3


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

    assert "Terraform generation is gated until an architecture diagram/definition exists." in result[0]
    assert isinstance(result[2], dict)
    assert result[2].get("skill_decision", {}).get("status") == "block"


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


def test_langgraph_orchestrator_module_falls_back_when_langgraph_unavailable(monkeypatch):
    import agent.langgraph_orchestrator as langgraph_orchestrator

    captured = {}

    async def _fake_legacy_run_turn(**kwargs):
        captured.update(kwargs)
        return {
            "reply": "legacy-path",
            "tool_calls": [],
            "artifacts": {},
            "history_length": 1,
        }

    monkeypatch.setattr(langgraph_orchestrator, "_HAS_LANGGRAPH", False)
    monkeypatch.setattr(orchestrator_agent, "run_turn", _fake_legacy_run_turn)

    result = asyncio.run(
        langgraph_orchestrator.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="hi",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=5,
            specialist_mode="langgraph",
        )
    )

    assert result["reply"] == "legacy-path"
    assert captured["specialist_mode"] == "langgraph"


def test_orchestrator_parallel_plan_detects_pov_and_jep_only():
    plan = orchestrator_agent._parallel_plan_for_message(
        "Please generate POV and JEP for this customer."
    )
    assert [p["tool"] for p in plan] == ["generate_pov", "generate_jep"]

    blocked_plan = orchestrator_agent._parallel_plan_for_message(
        "Generate POV, JEP, and terraform."
    )
    assert blocked_plan == []


def test_orchestrator_parallel_plan_detects_bom_intent():
    plan = orchestrator_agent._parallel_plan_for_message(
        "Please generate a BOM for 8 OCPU and 128 GB RAM."
    )
    assert len(plan) == 1
    assert plan[0]["tool"] == "generate_bom"


def test_orchestrator_gates_unrequested_generation_tools(monkeypatch):
    calls: list[str] = []
    llm_calls = {"count": 0}

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        return (f"{tool_name}-ok", "", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        llm_calls["count"] += 1
        return '{"tool": "generate_terraform", "args": {"prompt":"now create terraform"}}'

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "diagram exists with baseline architecture",
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate a BOM for 16 OCPU, 256 GB RAM, 2 TB block storage, with load balancer.",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert calls == ["generate_bom"]
    assert result["reply"] == "generate_bom-ok"
    assert llm_calls["count"] == 0


def test_orchestrator_change_request_requires_confirmation() -> None:
    store = InMemoryObjectStore()
    store.put(
        "context/acme/context.json",
        (
            '{"schema_version":"1.0","customer_id":"acme","customer_name":"ACME Corp",'
            '"last_updated":"","agents":{"diagram":{"version":1},"waf":{"version":1},'
            '"terraform":{"version":1},"pov":{"version":1},"jep":{"version":1}}}'
        ).encode("utf-8"),
        "application/json",
    )
    orchestrator_agent._PENDING_UPDATE_WORKFLOWS.clear()

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="We forgot an element in the application and need to update the system.",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=2,
            specialist_mode="langgraph",
        )
    )

    assert "confirm update all" in result["reply"].lower()
    assert result["tool_calls"] == []
    assert orchestrator_agent._PENDING_UPDATE_WORKFLOWS["acme"]["tools"] == [
        "generate_diagram",
        "generate_waf",
        "generate_terraform",
        "generate_pov",
        "generate_jep",
    ]


def test_orchestrator_change_request_confirmation_executes_in_order(monkeypatch):
    calls: list[str] = []
    store = InMemoryObjectStore()
    orchestrator_agent._PENDING_UPDATE_WORKFLOWS.clear()
    orchestrator_agent._PENDING_UPDATE_WORKFLOWS["acme"] = {
        "tools": [
            "generate_diagram",
            "generate_waf",
            "generate_terraform",
            "generate_pov",
            "generate_jep",
        ],
        "change_request": "Add missing service element.",
        "created_at": "2026-04-22T00:00:00Z",
    }

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        return (f"{tool_name}-ok", "", {})

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="confirm update all",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=2,
            specialist_mode="langgraph",
        )
    )

    assert calls == [
        "generate_diagram",
        "generate_waf",
        "generate_terraform",
        "generate_pov",
        "generate_jep",
    ]
    assert len(result["tool_calls"]) == 5
    assert "executed the approved update sequence in order" in result["reply"].lower()
    assert "acme" not in orchestrator_agent._PENDING_UPDATE_WORKFLOWS


def test_orchestrator_runs_pov_jep_in_parallel(monkeypatch):
    calls = []
    store = InMemoryObjectStore()
    _seed_pov_context(store)

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        await asyncio.sleep(0.05)
        return (f"{tool_name}-ok", f"{tool_name}-key", {})

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        return "Done."

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        archie_memory,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes captured for customer",
    )

    start = time.perf_counter()
    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Please generate POV and JEP for this customer.",
            store=store,
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=2,
            specialist_mode="langgraph",
        )
    )
    elapsed = time.perf_counter() - start

    assert sorted(calls) == ["generate_jep", "generate_pov"]
    # Parallel execution should complete much closer to one sleep interval.
    assert elapsed < 0.16
    assert len(result["tool_calls"]) == 2


def test_orchestrator_runs_bom_diagram_pairs_per_scenario(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = kwargs
        calls.append((tool_name, dict(args)))
        if tool_name == "generate_bom":
            scenario = "Scenario 1" if len([c for c in calls if c[0] == "generate_bom"]) == 1 else "Scenario 2"
            return (
                f"Final BOM prepared for {scenario}. Review line items, then export JSON or XLSX.",
                "",
                {
                    "type": "final",
                    "reply": f"Review line items for {scenario}.",
                    "trace_id": f"trace-{scenario[-1]}",
                    "bom_payload": {
                        "line_items": [
                            {"sku": "B94176", "description": f"{scenario} compute", "quantity": 4}
                        ],
                        "assumptions": [f"{scenario} assumption"],
                        "totals": {"estimated_monthly_cost": 1000},
                    },
                },
            )
        return (
            f"Diagram generated for {args['bom_text'].split(':', 1)[0]}.",
            f"agent3/acme/arch-{len([c for c in calls if c[0] == 'generate_diagram'])}/v1/diagram.drawio",
            {"render_manifest": {"node_count": 4}},
        )

    def _text_runner(_prompt: str, _system_message: str) -> str:
        raise AssertionError("Paired BOM/diagram workflow should not call the planner LLM")

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message=(
                "I want to do two things. 1. Full lift and shift to OCI, get off VMware. "
                "2. Direct migration using their stack but running on OCI. "
                "I need BOM and Diagram for each."
            ),
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert [tool for tool, _args in calls] == [
        "generate_bom",
        "generate_diagram",
        "generate_bom",
        "generate_diagram",
    ]
    first_diagram_text = calls[1][1]["bom_text"]
    second_diagram_text = calls[3][1]["bom_text"]
    assert "Full lift and shift to OCI" in first_diagram_text
    assert "Final BOM prepared for Scenario 1" in first_diagram_text
    assert "B94176" in first_diagram_text
    assert "Direct migration using their stack" in second_diagram_text
    assert "Final BOM prepared for Scenario 2" in second_diagram_text
    assert "upload or paste bom" not in result["reply"].lower()
    assert [call["scenario_label"] for call in result["tool_calls"]] == [
        "Scenario 1",
        "Scenario 1",
        "Scenario 2",
        "Scenario 2",
    ]


def test_bom_diagram_pair_does_not_treat_scenario_prompt_as_ungrounded_followup(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        _ = kwargs
        calls.append((tool_name, dict(args)))
        if tool_name == "generate_bom":
            return (
                "Final BOM prepared. Review line items.",
                "",
                {
                    "type": "final",
                    "reply": "Final BOM prepared for scenario.",
                    "bom_payload": {
                        "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                        "totals": {"estimated_monthly_cost": 500},
                    },
                },
            )
        return (
            "Diagram generated. Key: diagram.drawio",
            "diagram.drawio",
            {"render_manifest": {"node_count": 4}},
        )

    def _text_runner(_prompt: str, _system_message: str) -> str:
        raise AssertionError("Scenario workflow should not call the planner LLM")

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "scenario request")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: {"overall_status": "pass", "overall_pass": True},
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message=(
                "I want to do two things. 1. Full lift and shift to OCI, so get off of VMware. "
                "2. Direct migration using their stack but running on OCI. I need BOM and Diagram for each."
            ),
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="legacy",
        )
    )

    assert [tool for tool, _args in calls] == [
        "generate_bom",
        "generate_diagram",
        "generate_bom",
        "generate_diagram",
    ]
    first_bom_args = calls[0][1]
    assert first_bom_args["_bom_context_source"] == "scenario_request"
    assert first_bom_args["_bom_grounded_from_context"] is True
    assert "_bom_direct_reply" not in first_bom_args
    assert "Architecture diagram: skipped" not in result["reply"]


def test_fresh_generation_request_supersedes_stale_specialist_checkpoint(monkeypatch):
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "stale-specialist",
            "type": "specialist_questions",
            "status": "pending",
            "tool_name": "generate_bom",
            "tool_args": {"prompt": "old unresolved request"},
            "original_request": "old unresolved request",
            "questions": [
                {
                    "question_id": "generate_bom.q1",
                    "question": "Please share workload sizing.",
                    "blocking": True,
                }
            ],
            "prompt": "stale pending specialist questions",
        },
    )
    context_store.set_open_questions(
        ctx,
        [
            {
                "question_id": "generate_bom.q1",
                "question": "Please share workload sizing.",
                "blocking": True,
            }
        ],
    )
    context_store.write_context(store, "acme", ctx)
    calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        _ = kwargs
        calls.append((tool_name, dict(args)))
        if tool_name == "generate_bom":
            return (
                "Final BOM prepared. Review line items.",
                "",
                {
                    "type": "final",
                    "reply": "Final BOM prepared for scenario.",
                    "bom_payload": {
                        "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                        "totals": {"estimated_monthly_cost": 500},
                    },
                },
            )
        return (
            "Diagram generated. Key: diagram.drawio",
            "diagram.drawio",
            {"render_manifest": {"node_count": 4}},
        )

    def _text_runner(_prompt: str, _system_message: str) -> str:
        raise AssertionError("Fresh workflow should not answer the stale checkpoint or call the planner LLM")

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "scenario request")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: {"overall_status": "pass", "overall_pass": True},
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message=(
                "ok, I want to do two things. 1. Full lift and shift to OCI, so get off of VMware. "
                "2. direct migration using their stack but running on OCI. I need BOM and Diagram for each."
            ),
            store=store,
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="legacy",
        )
    )

    assert [tool for tool, _args in calls] == [
        "generate_bom",
        "generate_diagram",
        "generate_bom",
        "generate_diagram",
    ]
    assert "stale pending specialist questions" not in result["reply"]
    assert "Architecture diagram: skipped" not in result["reply"]
    updated = context_store.read_context(store, "acme", "ACME Corp")
    assert updated["pending_checkpoint"] is None
    assert updated["archie"]["open_questions"] == []


def test_orchestrator_blocks_completion_when_postflight_fails(monkeypatch):
    calls = {"count": 0}
    store = InMemoryObjectStore()
    _seed_pov_context(store)

    async def _fake_execute_tool_core(*_args, **_kwargs):
        calls["count"] += 1
        return ("POV generated", "pov/acme/v1.md", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        return '{"tool": "generate_pov", "args": {}}'

    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Please draft POV",
            store=store,
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert calls["count"] == 1
    assert "could not verify a persisted document artifact" in result["reply"].lower()
    assert result["artifacts"] == {}


def test_orchestrator_blocks_preflight_and_skips_tool_execution(monkeypatch):
    calls = {"count": 0}

    async def _fake_execute_tool_core(*_args, **_kwargs):
        calls["count"] += 1
        return ("unexpected", "", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        return '{"tool": "generate_diagram", "args": {}}'

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "")

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate diagram now",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert calls["count"] == 0
    assert "i need topology context" in result["reply"].lower()


def test_orchestrator_runs_bom_diagram_waf_in_prerequisite_order(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = kwargs
        calls.append((tool_name, dict(args)))
        if tool_name == "generate_bom":
            return (
                "Final BOM prepared. OKE, load balancer, database.",
                "",
                {
                    "type": "final",
                    "reply": "OKE BOM",
                    "bom_payload": {
                        "line_items": [{"sku": "B94176", "description": "OKE worker", "quantity": 3}],
                        "totals": {"estimated_monthly_cost": 1200},
                    },
                },
            )
        if tool_name == "generate_diagram":
            return ("Diagram generated. Key: diagram.drawio", "diagram.drawio", {"render_manifest": {"node_count": 5}})
        return ("WAF review saved. Key: waf.md", "waf.md", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        raise AssertionError("Prerequisite workflow should not call the planner LLM")

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate a BOM, diagram, and WAF for an OKE app with public load balancer and private database.",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert [tool for tool, _args in calls] == ["generate_bom", "generate_diagram", "generate_waf"]
    assert "Final BOM prepared" in calls[1][1]["bom_text"]
    assert "WAF review saved" in result["reply"]


def test_orchestrator_runs_diagram_before_waf_without_existing_diagram(monkeypatch):
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        if tool_name == "generate_diagram":
            return ("Diagram generated. Key: diagram.drawio", "diagram.drawio", {"render_manifest": {"node_count": 4}})
        return ("WAF review saved. Key: waf.md", "waf.md", {})

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate a diagram and WAF for an OKE app with WAF, public load balancer, private subnets, and Autonomous Database.",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert calls == ["generate_diagram", "generate_waf"]
    assert "WAF review saved" in result["reply"]


def test_orchestrator_terraform_without_bounded_scope_asks_before_running(monkeypatch):
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        return ("unexpected", "", {})

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate Terraform for the architecture.",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert calls == []
    assert "module boundary" in result["reply"].lower()
    assert "state backend" in result["reply"].lower()


def test_orchestrator_pov_jep_without_context_asks_before_running(monkeypatch):
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        return ("unexpected", "", {})

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Generate POV and JEP for this customer.",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            max_tool_iterations=3,
            specialist_mode="langgraph",
        )
    )

    assert calls == []
    assert "engagement context" in result["reply"].lower()
    assert "Management Summary" not in result["reply"]


def test_required_gstack_skills_have_quality_and_critic_guidance():
    specs = {spec.name: spec for spec in skill_loader.discover_skills()}
    for name in REQUIRED_GSTACK_SKILLS:
        spec = specs[name]
        version = tuple(int(part) for part in str(spec.metadata.get("version", "0")).split("."))
        assert version >= (1, 1)
        assert spec.sections.get("Quality Bar", "").strip()
        assert spec.sections.get("Critic Evaluation Guidance", "").strip()


def test_react_prompt_includes_internal_orchestrator_self_guidance():
    decision_context = {
        "goal": "Generate OCI diagram and WAF review",
        "constraints": {"security_requirements": ["private subnets"]},
        "assumptions": [],
        "success_criteria": ["Well-Architected risks are visible"],
    }
    prompt = orchestrator_agent._build_prompt(
        [],
        "",
        "Generate a diagram and WAF for private OKE with public ingress.",
        decision_context=decision_context,
    )

    assert prompt.startswith("[Internal Orchestrator Self-Guidance")
    assert "[Internal Plan]" in prompt
    assert "Requested deliverables: Architecture diagram, Well-Architected review" in prompt
    assert "Selected skills:" in prompt
    assert "orchestrator" in prompt
    assert "diagram_for_oci" in prompt
    assert "oci_waf_reviewer" in prompt
    assert "Relevant WAF pillars:" in prompt


def test_react_followup_prompt_preserves_internal_orchestrator_self_guidance():
    decision_context = {
        "goal": "Generate OCI diagram",
        "constraints": {"security_requirements": ["private subnets"]},
        "assumptions": [],
        "success_criteria": ["Draw.io artifact is generated"],
    }
    prompt = orchestrator_agent._build_prompt(
        [],
        "",
        "Generate a diagram for private OKE.",
        decision_context=decision_context,
    )
    prefix = prompt.split("[End Internal Orchestrator Self-Guidance]", 1)[0]

    followup = orchestrator_agent._append_tool_result(
        prompt,
        "generate_diagram",
        "Diagram generated. Key: diagrams/acme/oci_architecture/v1/diagram.drawio",
    )

    assert followup.startswith(prefix)
    assert followup.count("[Internal Orchestrator Self-Guidance") == 1
    assert "[Tool result: generate_diagram]" in followup
    assert followup.rstrip().endswith("ASSISTANT:")


def test_skill_injection_applies_for_terraform_prompt():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_terraform",
        {"prompt": "Build VCN"},
        user_message="Generate terraform for OCI",
    )
    assert "terraform_for_oci" in injected.get("_skill_injected", [])
    assert "orchestrator" in injected.get("_skill_injected", [])
    assert "Injected Skill Guidance" in injected.get("prompt", "")
    assert "Build VCN" in injected.get("prompt", "")
    assert injected.get("_skill_model_profile") == "terraform"


def test_skill_injection_applies_model_profile_for_pov():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_pov",
        {"feedback": "tighten wording"},
        user_message="Generate POV for exec stakeholder readout",
    )
    assert "oci_customer_pov_writer" in injected.get("_skill_injected", [])
    assert "orchestrator" in injected.get("_skill_injected", [])
    assert injected.get("_skill_model_profile") == "pov"


def test_skill_injection_applies_model_profile_for_jep():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_jep",
        {"feedback": "focus milestones"},
        user_message="Generate JEP for OCI POC",
    )
    assert "oci_jep_writer" in injected.get("_skill_injected", [])
    assert "orchestrator" in injected.get("_skill_injected", [])
    assert injected.get("_skill_model_profile") == "jep"


def test_skill_injection_applies_model_profile_for_waf():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_waf",
        {"feedback": "tighten findings"},
        user_message="Run OCI WAF review",
    )
    assert "oci_waf_reviewer" in injected.get("_skill_injected", [])
    assert "orchestrator" in injected.get("_skill_injected", [])
    assert injected.get("_skill_model_profile") == "waf"


def test_runner_for_tool_uses_profile_aware_runner():
    called = {}

    def _profiled_runner(prompt: str, system_message: str, model_profile: str = "orchestrator") -> str:
        called["profile"] = model_profile
        return f"{model_profile}:{prompt[:10]}"

    runner = orchestrator_agent._runner_for_tool(
        _profiled_runner,
        {"_skill_model_profile": "terraform"},
    )
    out = runner("Generate module", "system")
    assert out.startswith("terraform:")
    assert called.get("profile") == "terraform"


def test_orchestrator_critic_refines_once(monkeypatch):
    calls = {"count": 0}
    store = InMemoryObjectStore()
    _seed_pov_context(store)

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        calls["count"] += 1
        _ = (tool_name, args)
        if calls["count"] == 1:
            return ("POV v1 saved. Key: pov/acme/v1.md", "pov/acme/v1.md", {"version": 1})
        return ("POV v2 saved. Key: pov/acme/v2.md", "pov/acme/v2.md", {"version": 2})

    critic_results = iter(
        [
            {
                "issues": ["Too generic on business outcomes."],
                "severity": "medium",
                "suggestions": ["Add measurable business outcomes."],
                "confidence": 82,
                "overall_pass": False,
                "critique_summary": "Need clearer business impact metrics.",
            },
            {
                "issues": [],
                "severity": "low",
                "suggestions": [],
                "confidence": 90,
                "overall_pass": True,
                "critique_summary": "Acceptable.",
            },
        ]
    )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: next(critic_results),
    )

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {"feedback": "initial pass"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate POV",
        )
    )

    assert calls["count"] == 2
    assert "v2" in summary
    assert key == "pov/acme/v2.md"
    assert data.get("critic_retry", {}).get("attempt") == 1
    assert data.get("refinement_count") == 1
    assert isinstance(data.get("critic_history"), list)
    assert isinstance(data.get("trace"), dict)
    assert data["trace"].get("max_refinements") == 3


def test_skill_injection_applies_for_diagram():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_diagram",
        {"bom_text": "VCN with private subnet and LB"},
        user_message="Generate an OCI architecture diagram",
    )
    assert "Injected Skill Guidance" in injected.get("bom_text", "")
    assert injected.get("_skill_injected")
    assert "diagram_for_oci" in injected.get("_skill_injected", [])
    assert "orchestrator" in injected.get("_skill_injected", [])
    assert isinstance(injected.get("_skill_sections"), dict)


def test_execute_tool_blocks_diagram_without_selected_standards_bundle(monkeypatch):
    monkeypatch.setattr(
        archie_loop,
        "_build_expert_mode_metadata",
        lambda **_kwargs: {"enabled": True, "reference_mode": "reference-backed"},
    )

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "VCN with private subnet and LB"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate diagram",
        )
    )

    assert "no Oracle standards bundle is selected" in summary
    assert key == ""
    assert data["reference_mode"] == "blocked"


def test_execute_tool_diagram_trace_includes_reference_metadata(monkeypatch):
    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return ("Diagram generated. Key: diagrams/acme/v1/diagram.drawio", "diagrams/acme/v1/diagram.drawio", {})

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "diagram notes exist")
    monkeypatch.setattr(
        archie_loop,
        "_build_expert_mode_metadata",
        lambda **_kwargs: {
            "enabled": True,
            "tool_name": "generate_diagram",
            "mandatory_skill_injection": True,
            "standards_bundle_version": "2026.04.24",
            "reference_family": "classic_3tier_webapp",
            "reference_confidence": 0.88,
            "reference_mode": "reference-backed",
            "family_constraints": {"connector_lanes": ["internet_to_ingress"]},
        },
    )

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Load balancer, compute, database"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate diagram",
        )
    )

    assert "Diagram generated" in summary
    assert key.endswith("diagram.drawio")
    assert data["trace"]["standards_bundle_version"] == "2026.04.24"
    assert data["trace"]["reference_family"] == "classic_3tier_webapp"
    assert data["trace"]["reference_mode"] == "reference-backed"
    assert data["trace"]["reference_confidence"] == 0.88


def test_execute_tool_diagram_trace_preserves_backend_error_metadata(monkeypatch):
    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return (
            "I could not complete the diagram because the drawing backend rejected the current topology inputs.\n"
            "Backend failure: Cross-region invariant violation: active-active with a single writable database is unsupported.",
            "",
            {
                "backend_error_message": (
                    "Cross-region invariant violation: active-active with a single writable database is unsupported."
                ),
                "diagram_recovery_status": "backend_error",
                "diagram_final_disposition": "backend_error",
                "assumptions_used": [
                    {
                        "id": "diagram_multi_region_posture_default",
                        "statement": "Multi-region posture not specified; assume active-passive HA/DR across two OCI regions.",
                        "reason": "default",
                        "risk": "medium",
                    }
                ],
                "recovery_attempt_count": 1,
            },
        )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "diagram notes exist")

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Generate an active-active multi-region OKE diagram."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate diagram",
        )
    )

    assert key == ""
    assert "backend rejected" in summary.lower()
    assert data["trace"]["backend_error_message"].startswith("Cross-region invariant violation")
    assert data["trace"]["diagram_recovery_status"] == "backend_error"
    assert data["trace"]["recovery_attempt_count"] == 1
    assert data["trace"]["final_disposition"] == "backend_error"


def test_execute_tool_diagram_artifact_review_retries_missing_bm_split_fd(monkeypatch):
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.set_archie_engagement_summary(ctx, "OCI diagram exists for OCVS migration.")
    context_store.write_context(store, "acme", ctx)
    calls: list[dict] = []

    bad_key = "diagrams/acme/oci_architecture/v1/diagram.drawio"
    good_key = "diagrams/acme/oci_architecture/v2/diagram.drawio"
    bad_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="fd1" value="Fault Domain 1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="Fault Domain 2" vertex="1" parent="1"/>
      <mxCell id="compute" value="Generic Compute" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """
    good_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="fd1" value="Fault Domain 1" vertex="1" parent="1"/>
      <mxCell id="bm1" value="BM.Standard.X9.64 #1 FD1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="Fault Domain 2" vertex="1" parent="1"/>
      <mxCell id="bm2" value="BM.Standard.X9.64 #2 FD2" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        calls.append(dict(args))
        target_store = kwargs["store"]
        if len(calls) == 1:
            target_store.put(bad_key, bad_xml.encode("utf-8"), "text/xml")
            return (
                f"Diagram generated. Key: {bad_key}",
                bad_key,
                {"render_manifest": {"node_count": 3}},
            )
        target_store.put(good_key, good_xml.encode("utf-8"), "text/xml")
        return (
            f"Diagram generated. Key: {good_key}",
            good_key,
            {"render_manifest": {"node_count": 4}},
        )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "diagram notes exist")
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Update the diagram to have the 2 BM servers in split FD."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Update the diagram to have the 2 BM servers in split FD.",
        )
    )

    assert len(calls) == 2
    assert "Archie Diagram Artifact Review Feedback" in calls[1]["bom_text"]
    assert key == good_key
    assert summary == f"Diagram generated. Key: {good_key}"
    assert data["trace"]["review_verdict"] == "pass"
    assert data["trace"]["review_produced"]["bm_count"] >= 2
    assert data["trace"]["refinement_history"]


def test_execute_tool_diagram_retry_promotes_ocvs_review_feedback_to_user_notes(monkeypatch):
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.set_archie_engagement_summary(ctx, "OCVS migration from VMware/VxRail to OCI Dedicated VMware Solution.")
    context_store.write_context(store, "acme", ctx)
    calls: list[dict] = []

    bad_key = "diagrams/acme/oci_architecture/v1/diagram.drawio"
    good_key = "diagrams/acme/oci_architecture/v2/diagram.drawio"
    bad_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="fd1" value="Fault Domain 1" vertex="1" parent="1"/>
      <mxCell id="bm1" value="BM.Standard.X9.64 host #1 FD1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="Fault Domain 2" vertex="1" parent="1"/>
      <mxCell id="bm2" value="BM.Standard.X9.64 host #2 FD2" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """
    good_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="ocvs" value="OCI Dedicated VMware Solution SDDC, vCenter, NSX" vertex="1" parent="1"/>
      <mxCell id="fd1" value="Fault Domain 1" vertex="1" parent="1"/>
      <mxCell id="bm1" value="BM.Standard.X9.64 ESXi host #1 FD1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="Fault Domain 2" vertex="1" parent="1"/>
      <mxCell id="bm2" value="BM.Standard.X9.64 ESXi host #2 FD2" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        calls.append(dict(args))
        target_store = kwargs["store"]
        if len(calls) == 1:
            target_store.put(bad_key, bad_xml.encode("utf-8"), "text/xml")
            return (f"Diagram generated. Key: {bad_key}", bad_key, {"render_manifest": {"node_count": 4}})
        target_store.put(good_key, good_xml.encode("utf-8"), "text/xml")
        return (f"Diagram generated. Key: {good_key}", good_key, {"render_manifest": {"node_count": 5}})

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(
        archie_memory,
        "_build_context_summary_for_skills",
        lambda *_a, **_k: "OCVS migration from VMware/VxRail to OCI Dedicated VMware Solution.",
    )
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    user_message = (
        "Regenerate the diagram with the two BM.Standard.X9.64 hosts split across fault domains, "
        "using FD-local subnets so one BM host is in FD1 and the other is in FD2."
    )
    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": user_message},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message=user_message,
        )
    )

    assert len(calls) == 2
    retry_notes = calls[1]["_architect_brief"]["user_notes"]
    assert "Archie diagram acceptance corrections" in retry_notes
    assert "OCVS/VMware-specific elements" in retry_notes
    assert key == good_key
    assert summary == f"Diagram generated. Key: {good_key}"
    assert data["trace"]["review_verdict"] == "pass"


def test_execute_tool_diagram_auto_answers_ha_ads_for_explicit_bm_fd_request(monkeypatch):
    store = InMemoryObjectStore()
    calls: list[dict] = []
    good_key = "diagrams/acme/oci_architecture/v1/diagram.drawio"
    good_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="ocvs" value="OCI Dedicated VMware Solution SDDC, vCenter, NSX" vertex="1" parent="1"/>
      <mxCell id="fd1" value="FD1" vertex="1" parent="1"/>
      <mxCell id="bm1" value="BM.Standard.X9.64 ESXi Host - FD1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="FD2" vertex="1" parent="1"/>
      <mxCell id="bm2" value="BM.Standard.X9.64 ESXi Host - FD2" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        calls.append(dict(args))
        if len(calls) == 1:
            return (
                "Diagram clarification required before generation can continue.",
                "",
                {
                    "questions": [
                        {
                            "id": "ha.ads",
                            "question": "How should the BM hosts be placed across availability domains?",
                            "blocking": True,
                        }
                    ],
                    "diagram_recovery_status": "needs_clarification",
                    "diagram_final_disposition": "needs_clarification",
                },
            )
        kwargs["store"].put(good_key, good_xml.encode("utf-8"), "text/xml")
        return (f"Diagram generated. Key: {good_key}", good_key, {"drawio_xml": good_xml})

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(
        archie_memory,
        "_build_context_summary_for_skills",
        lambda *_a, **_k: "OCVS migration from VMware/VxRail to OCI Dedicated VMware Solution.",
    )
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    user_message = (
        "Regenerate the OCVS diagram in af-johannesburg-1 with two BM.Standard.X9.64 hosts: "
        "host 1 in FD1 using FD-local subnet and host 2 in FD2 using FD-local subnet."
    )
    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": user_message},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message=user_message,
        )
    )

    assert len(calls) == 2
    assert key == good_key
    assert summary == f"Diagram generated. Key: {good_key}"
    assert "archie_question_bundle" not in data
    auto_answers = data["archie_auto_answers"]
    assert auto_answers[0]["question_id"] == "ha.ads"
    assert auto_answers[0]["final_answer"].startswith("two BM.Standard.X9.64 hosts")
    assert "ha.ads" in calls[1]["bom_text"]


def test_execute_tool_diagram_artifact_review_blocks_after_failed_retry(monkeypatch):
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.set_archie_engagement_summary(ctx, "OCI diagram exists for OCVS migration.")
    context_store.write_context(store, "acme", ctx)
    calls = 0
    bad_xml = """
    <mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="fd1" value="Fault Domain 1" vertex="1" parent="1"/>
      <mxCell id="fd2" value="Fault Domain 2" vertex="1" parent="1"/>
      <mxCell id="compute" value="Generic Compute" vertex="1" parent="1"/>
    </root></mxGraphModel>
    """

    async def _fake_execute_tool_core(tool_name, args, **kwargs):
        nonlocal calls
        calls += 1
        key = f"diagrams/acme/oci_architecture/v{calls}/diagram.drawio"
        kwargs["store"].put(key, bad_xml.encode("utf-8"), "text/xml")
        return (f"Diagram generated. Key: {key}", key, {"render_manifest": {"node_count": 3}})

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "diagram notes exist")
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Update the diagram to have the 2 BM servers in split FD."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Update the diagram to have the 2 BM servers in split FD.",
        )
    )

    assert calls == 2
    assert key == ""
    assert "Archie expert review blocked" in summary
    assert "requested 2" in summary
    assert data["trace"]["review_verdict"] == "blocked"


def test_diagram_revision_complaint_routes_to_diagram_without_diagram_word(monkeypatch):
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/acme/oci_architecture/v1/diagram.drawio",
            "summary": "existing diagram",
        },
    )
    context_store.write_context(store, "acme", ctx)
    calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        calls.append((tool_name, dict(args)))
        return ("Diagram generated. Key: diagrams/acme/v2/diagram.drawio", "diagrams/acme/v2/diagram.drawio", {})

    monkeypatch.setattr(archie_loop, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="this does not show 2 BM servers",
            store=store,
            text_runner=lambda _prompt, _system: "I agree that change is needed.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [tool for tool, _args in calls] == ["generate_diagram"]
    assert calls[0][1]["bom_text"] == "this does not show 2 BM servers"
    assert result["tool_calls"][0]["tool"] == "generate_diagram"


def test_execute_tool_bom_expert_review_blocks_undersized_retry(monkeypatch):
    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return (
            "Final BOM prepared.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared.",
                "bom_payload": {
                    "currency": "USD",
                    "line_items": [
                        {"sku": "B94176", "description": "Compute E4 OCPU", "category": "compute", "quantity": 4},
                        {"sku": "B94177", "description": "Compute E4 Memory", "category": "compute", "quantity": 64},
                        {"sku": "B91961", "description": "Block storage capacity", "category": "storage", "quantity": 1024},
                        {"sku": "B91628", "description": "Object storage", "category": "storage", "quantity": 204.8},
                    ],
                },
            },
        )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "")
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "Generate BOM for 48 OCPU, 768 GB RAM, 42 TB storage."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate BOM for 48 OCPU, 768 GB RAM, 42 TB storage.",
        )
    )

    assert key == ""
    assert "Archie expert review blocked" in summary
    assert "requested 48" in summary
    assert "requested 768" in summary
    assert "requested 43008" in summary
    assert data["trace"]["review_verdict"] == "blocked"
    assert len(data["trace"]["refinement_history"]) == 1


def test_execute_tool_bom_expert_review_passes_matching_sizing(monkeypatch):
    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return (
            "Final BOM prepared.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared.",
                "bom_payload": {
                    "currency": "USD",
                    "line_items": [
                        {"sku": "B94176", "description": "Compute E4 OCPU", "category": "compute", "quantity": 48},
                        {"sku": "B94177", "description": "Compute E4 Memory", "category": "compute", "quantity": 768},
                        {"sku": "B91961", "description": "Block storage capacity", "category": "storage", "quantity": 43008},
                    ],
                },
            },
        )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "")
    monkeypatch.setattr(orchestrator_agent.critic_agent, "evaluate_tool_result", lambda **_kwargs: {"overall_pass": True})

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "Generate BOM for 48 OCPU, 768 GB RAM, 42 TB storage."},
            customer_id="acme",
            customer_name="ACME Corp",
            store=InMemoryObjectStore(),
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate BOM for 48 OCPU, 768 GB RAM, 42 TB storage.",
        )
    )

    assert "Final BOM prepared" in summary
    assert key == ""
    assert data["trace"]["review_verdict"] == "pass"
    assert data["trace"]["archie_lens"] == "OCI BOM sizing and pricing reviewer"
    assert data["trace"]["sent_to_specialist"]["prompt"].startswith("Generate BOM")


def test_orchestrator_critic_respects_max_refinements(monkeypatch):
    calls = {"count": 0}
    store = InMemoryObjectStore()
    _seed_pov_context(store)

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        calls["count"] += 1
        _ = (tool_name, args)
        return (f"POV v{calls['count']} saved. Key: pov/acme/v{calls['count']}.md", f"pov/acme/v{calls['count']}.md", {})

    critic_results = iter(
        [
            {
                "issues": ["Issue 1"],
                "severity": "high",
                "suggestions": ["Fix 1"],
                "confidence": 70,
                "overall_pass": False,
                "critique_summary": "Not enough detail.",
            },
            {
                "issues": ["Issue 2"],
                "severity": "high",
                "suggestions": ["Fix 2"],
                "confidence": 70,
                "overall_pass": False,
                "critique_summary": "Still not enough detail.",
            },
        ]
    )

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: next(critic_results),
    )

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {"feedback": "initial pass"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate POV",
            max_refinements=1,
        )
    )
    assert calls["count"] == 2  # initial + one retry
    assert data.get("refinement_count") == 1
    assert data.get("best_effort") is True
    assert "best-effort" in summary.lower()


def test_orchestrator_critic_fail_open_on_error(monkeypatch):
    store = InMemoryObjectStore()
    _seed_pov_context(store)

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return ("POV v1 saved. Key: pov/acme/v1.md", "pov/acme/v1.md", {})

    monkeypatch.setattr(archie_loop, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(archie_memory, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("critic parse failed")),
    )

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {"feedback": "initial pass"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=_dummy_text_runner,
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate POV",
        )
    )
    assert "v1" in summary
    assert key == "pov/acme/v1.md"
    assert any("critic_error_fail_open" in w for w in data.get("warnings", []))


def test_dynamic_skill_selector_prefers_tool_tag(tmp_path: Path):
    root = tmp_path / "skills"
    a = root / "alpha"
    b = root / "beta"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "SKILL.md").write_text(
        "---\n"
        "tool_tags: generate_terraform\n"
        "model_profile: terraform\n"
        "keywords: terraform,oci,network\n"
        "---\n"
        "# alpha\nterraform skill\n",
        encoding="utf-8",
    )
    (b / "SKILL.md").write_text(
        "---\n"
        "tool_tags: generate_pov\n"
        "model_profile: pov\n"
        "keywords: writing,executive\n"
        "---\n"
        "# beta\npov skill\n",
        encoding="utf-8",
    )
    selected = skill_loader.select_skills_for_call(
        tool_name="generate_terraform",
        user_message="Need OCI terraform networking baseline",
        tool_args={"prompt": "build vcn"},
        skill_root=root,
    )
    assert selected
    assert selected[0].name == "alpha"


def test_parse_tool_call_accepts_tool_use_block():
    raw = (
        "<tool_use>\n"
        '{"name":"generate_terraform","args":{"prompt":"baseline vcn"}}\n'
        "</tool_use>"
    )
    parsed = orchestrator_agent._parse_tool_call(raw)
    assert parsed == {"tool": "generate_terraform", "args": {"prompt": "baseline vcn"}}

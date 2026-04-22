from __future__ import annotations

import asyncio
import time
from pathlib import Path

from drawing_agent_server import _run_orchestrator_turn, OrchestratorChatRequest
from agent.persistence_objectstore import InMemoryObjectStore
import agent.orchestrator_agent as orchestrator_agent
from agent import skill_loader


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
    monkeypatch.setattr(
        orchestrator_agent,
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
    outputs = iter(
        [
            '{"tool": "generate_terraform", "args": {"prompt":"now create terraform"}}',
            "Done.",
        ]
    )

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        return (f"{tool_name}-ok", "", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        return next(outputs)

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        orchestrator_agent,
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
    assert "Skipped `generate_terraform` because it was not requested." in result["reply"]


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

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

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

    async def _fake_execute_tool(tool_name, args, **kwargs):
        _ = (args, kwargs)
        calls.append(tool_name)
        await asyncio.sleep(0.05)
        return (f"{tool_name}-ok", f"{tool_name}-key", {})

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        return "Done."

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(
        orchestrator_agent,
        "_build_context_summary_for_skills",
        lambda *_args, **_kwargs: "notes captured for customer",
    )

    start = time.perf_counter()
    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Please generate POV and JEP for this customer.",
            store=InMemoryObjectStore(),
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


def test_orchestrator_blocks_completion_when_postflight_fails(monkeypatch):
    calls = {"count": 0}

    async def _fake_execute_tool_core(*_args, **_kwargs):
        calls["count"] += 1
        return ("POV generated", "pov/acme/v1.md", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        return '{"tool": "generate_pov", "args": {}}'

    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Please draft POV",
            store=InMemoryObjectStore(),
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

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "")

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
    assert "please upload or paste bom/resource details first" in result["reply"].lower()


def test_skill_injection_applies_for_terraform_prompt():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_terraform",
        {"prompt": "Build VCN"},
        user_message="Generate terraform for OCI",
    )
    assert "terraform_for_oci" in injected.get("_skill_injected", [])
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
    assert injected.get("_skill_model_profile") == "pov"


def test_skill_injection_applies_model_profile_for_jep():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_jep",
        {"feedback": "focus milestones"},
        user_message="Generate JEP for OCI POC",
    )
    assert "oci_jep_writer" in injected.get("_skill_injected", [])
    assert injected.get("_skill_model_profile") == "jep"


def test_skill_injection_applies_model_profile_for_waf():
    injected = orchestrator_agent._inject_skill_into_tool_args(
        "generate_waf",
        {"feedback": "tighten findings"},
        user_message="Run OCI WAF review",
    )
    assert "oci_waf_reviewer" in injected.get("_skill_injected", [])
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

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
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
            store=InMemoryObjectStore(),
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
    assert isinstance(injected.get("_skill_sections"), dict)


def test_orchestrator_critic_respects_max_refinements(monkeypatch):
    calls = {"count": 0}

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

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
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
            store=InMemoryObjectStore(),
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
    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return ("POV v1 saved. Key: pov/acme/v1.md", "pov/acme/v1.md", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
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
            store=InMemoryObjectStore(),
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

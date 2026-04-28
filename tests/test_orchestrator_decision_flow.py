from __future__ import annotations

import asyncio

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent import context_store
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.integration


class _FakeBomService:
    def __init__(self, responses, *, refresh_result=None, refresh_error: Exception | None = None) -> None:
        self._responses = list(responses)
        self.refresh_result = refresh_result if refresh_result is not None else {"ready": True, "source": "fallback"}
        self.refresh_error = refresh_error
        self.chat_messages: list[str] = []
        self.refresh_calls = 0

    def health(self) -> dict[str, object]:
        return {"ready": False, "source": "none", "pricing_sku_count": 0}

    def chat(self, *, message: str, **_kwargs):
        self.chat_messages.append(message)
        if not self._responses:
            raise AssertionError("No fake BOM responses remaining")
        return self._responses.pop(0)

    def refresh_data(self) -> dict[str, object]:
        self.refresh_calls += 1
        if self.refresh_error is not None:
            raise self.refresh_error
        return dict(self.refresh_result)


def test_run_turn_persists_pending_cost_checkpoint(monkeypatch) -> None:
    responses = iter(
        [
            '{"tool": "generate_bom", "args": {"prompt": "Build BOM"}}',
        ]
    )

    def _text_runner(prompt: str, system_message: str) -> str:
        _ = (prompt, system_message)
        return next(responses, "Checkpoint needed.")

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        _ = (tool_name, args)
        return (
            "Final BOM prepared. Review the line items.",
            "",
            {"bom_payload": {"totals": {"estimated_monthly_cost": 7200}}},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: {
            "overall_status": "checkpoint_required",
            "security": {"status": "pass", "findings": [], "required_actions": []},
            "cost": {
                "status": "checkpoint_required",
                "estimated_monthly_cost": 7200,
                "budget_target": 5000,
                "variance": 2200,
                "findings": ["Budget exceeded."],
            },
            "quality": {
                "status": "pass",
                "issues": [],
                "suggestions": [],
                "confidence": 95,
                "summary": "Acceptable",
                "severity": "low",
            },
            "decision_summary": "Cost checkpoint required.",
            "reason_codes": ["budget_exceeded"],
            "overall_pass": True,
            "confidence": 95,
            "issues": [],
            "suggestions": [],
            "critique_summary": "Acceptable",
            "severity": "low",
        },
    )

    store = InMemoryObjectStore()
    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="Build a BOM under $5000 monthly in us-phoenix-1",
            store=store,
            text_runner=_text_runner,
            max_tool_iterations=2,
            specialist_mode="legacy",
        )
    )

    assert "Cost checkpoint required" in result["reply"]
    assert len(result["tool_calls"]) == 1
    trace = result["tool_calls"][0]["result_data"]["trace"]
    assert trace["governor"]["overall_status"] == "checkpoint_required"
    assert trace["checkpoint"]["status"] == "pending"
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    assert ctx["pending_checkpoint"]["status"] == "pending"
    assert ctx["latest_decision_context"]["constraints"]["cost_max_monthly"] == 5000.0


def test_run_turn_checkpoint_approval_clears_pending(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "cp-1",
            "type": "cost_override",
            "status": "pending",
            "prompt": "Cost checkpoint required.",
            "recommended_action": "approve or revise input",
            "options": ["approve checkpoint", "revise input"],
            "decision_context_hash": "abc123",
        },
    )
    context_store.write_context(store, "acme", ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="acme",
            customer_name="ACME Corp",
            user_message="approve checkpoint",
            store=store,
            text_runner=lambda _prompt, _system: "ok",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "Checkpoint approved" in result["reply"]
    updated = context_store.read_context(store, "acme", "ACME Corp")
    assert updated["pending_checkpoint"] is None
    assert updated["decision_log"][-1]["checkpoint_status"] == "approved"


def test_checkpoint_from_result_skips_sparse_draft_checkpoint_without_high_risk_signal() -> None:
    checkpoint = orchestrator_agent._checkpoint_from_result(
        tool_name="generate_bom",
        decision_context={
            "goal": "Need a ballpark BOM and diagram from rough notes.",
            "assumption_mode": True,
            "constraints": {"region": None, "availability_target": None, "cost_max_monthly": None},
            "assumptions": [
                {
                    "id": "region_default",
                    "statement": "Region not specified; assume primary OCI region from current tenancy preference.",
                    "reason": "No region supplied.",
                    "risk": "medium",
                },
                {
                    "id": "availability_default",
                    "statement": "Availability target assumed at 99.9%.",
                    "reason": "No availability target supplied.",
                    "risk": "low",
                },
            ],
            "success_criteria": [],
            "missing_inputs": ["preferred OCI region"],
            "requires_user_confirmation": False,
        },
        result_data={
            "governor": {
                "overall_status": "checkpoint_required",
                "decision_summary": "Best-effort BOM is assumption-heavy.",
                "cost": {
                    "status": "checkpoint_required",
                    "estimated_monthly_cost": None,
                    "budget_target": None,
                    "variance": None,
                    "findings": [],
                },
            }
        },
    )

    assert checkpoint is None


def test_checkpoint_from_result_keeps_assumption_review_for_high_risk_signal() -> None:
    checkpoint = orchestrator_agent._checkpoint_from_result(
        tool_name="generate_terraform",
        decision_context={
            "goal": "Draft Terraform for a compliance-sensitive OCI environment.",
            "constraints": {
                "region": "us-phoenix-1",
                "availability_target": "99.9%",
                "cost_max_monthly": None,
                "compliance_requirements": ["pci"],
            },
            "assumptions": [
                {
                    "id": "security_baseline",
                    "statement": "Security model is not fully specified; assume private-only networking and least privilege.",
                    "reason": "No final security architecture was approved.",
                    "risk": "high",
                }
            ],
            "success_criteria": [],
            "missing_inputs": ["approved security controls"],
            "requires_user_confirmation": True,
        },
        result_data={
            "governor": {
                "overall_status": "checkpoint_required",
                "decision_summary": "Terraform draft depends on unapproved security assumptions.",
                "cost": {
                    "status": "pass",
                    "estimated_monthly_cost": None,
                    "budget_target": None,
                    "variance": None,
                    "findings": [],
                },
            }
        },
    )

    assert checkpoint is not None
    assert checkpoint["type"] == "assumption_review"
    assert "Discovery checkpoint required before final acceptance." in checkpoint["prompt"]
    assert "Security model is not fully specified" in checkpoint["prompt"]


def test_generate_bom_followup_uses_latest_diagram_and_auto_refreshes(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/acme/oci_architecture/v1/diagram.drawio",
            "node_count": 8,
            "deployment_summary": "single_ad, 8 nodes",
            "reference_family": "single_region_oke_app",
            "reference_mode": "matched",
            "decision_context_summary": "Goal: build an OCI OKE application platform with ingress and data tier.",
            "assumptions_used": [
                {
                    "id": "region_default",
                    "statement": "Region not specified; assume the tenancy primary OCI region.",
                    "risk": "medium",
                }
            ],
        },
    )
    context_store.write_context(store, "acme", ctx)

    fake_service = _FakeBomService(
        [
            {
                "type": "normal",
                "reply": "BOM data is not ready. Run /api/bom/refresh-data, then retry.",
                "trace": {"cache_ready": False, "cache_source": "none"},
            },
            {
                "type": "final",
                "reply": "Review line items, then export JSON or XLSX.",
                "trace_id": "trace-123",
                "trace": {"cache_ready": True, "cache_source": "fallback"},
                "bom_payload": {
                    "line_items": [{"sku": "B94176"}, {"sku": "B94177"}],
                    "assumptions": ["Ballpark defaults applied."],
                    "totals": {"estimated_monthly_cost": 1234.5},
                },
            },
        ]
    )
    monkeypatch.setattr(orchestrator_agent, "get_shared_bom_service", lambda: fake_service)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "I need a BOM for this"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="I need a BOM for this",
        )
    )

    assert summary.startswith("Final BOM prepared.")
    assert fake_service.refresh_calls == 1
    assert "Generate BOM for the latest OCI architecture diagram." in fake_service.chat_messages[0]
    assert "single_region_oke_app" in fake_service.chat_messages[0]
    assert "/api/bom/refresh-data" not in summary
    assert data["trace"]["bom_context_source"] == "latest_diagram"
    assert data["trace"]["bom_cache_refresh_attempted"] is True
    assert data["trace"]["bom_cache_refresh_status"] == "succeeded"
    assert data["trace"]["bom_retry_count"] == 1
    assert data["trace"]["bom_retry_succeeded"] is True

    refreshed_ctx = context_store.read_context(store, "acme", "ACME Corp")
    bom_ctx = refreshed_ctx["agents"]["bom"]
    assert bom_ctx["estimated_monthly_cost"] == 1234.5
    assert bom_ctx["context_source"] == "latest_diagram"
    summary_text = context_store.build_context_summary(refreshed_ctx)
    assert "Architecture Diagram" in summary_text
    assert "BOM (v1)" in summary_text


def test_generate_bom_followup_refresh_failure_stays_internal(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "acme", "ACME Corp")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/acme/oci_architecture/v1/diagram.drawio",
            "node_count": 5,
            "deployment_summary": "single_ad, 5 nodes",
        },
    )
    context_store.write_context(store, "acme", ctx)

    fake_service = _FakeBomService(
        [
            {
                "type": "normal",
                "reply": "BOM data is not ready. Run /api/bom/refresh-data, then retry.",
                "trace": {"cache_ready": False, "cache_source": "none"},
            }
        ],
        refresh_error=RuntimeError("upstream unavailable"),
    )
    monkeypatch.setattr(orchestrator_agent, "get_shared_bom_service", lambda: fake_service)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "I need a BOM for this"},
            customer_id="acme",
            customer_name="ACME Corp",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="I need a BOM for this",
        )
    )

    assert "could not initialize the internal oci bom pricing data" in summary.lower()
    assert "/api/bom/refresh-data" not in summary
    assert fake_service.refresh_calls == 1
    assert data["trace"]["bom_cache_refresh_attempted"] is True
    assert data["trace"]["bom_cache_refresh_status"] == "failed"
    assert data["trace"]["bom_retry_count"] == 0


def test_generate_bom_followup_without_prior_diagram_asks_for_context(monkeypatch) -> None:
    fake_service = _FakeBomService([])
    monkeypatch.setattr(orchestrator_agent, "get_shared_bom_service", lambda: fake_service)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "I need a BOM for this"},
            customer_id="empty",
            customer_name="Empty Corp",
            store=InMemoryObjectStore(),
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="I need a BOM for this",
        )
    )

    assert "`this` is not grounded to a prior diagram or workload yet" in summary
    assert "rough sizing for OCPU, memory, storage" in summary
    assert fake_service.refresh_calls == 0
    assert fake_service.chat_messages == []
    assert data["trace"]["bom_context_source"] == "unresolved_followup"


def test_hydrate_tool_args_builds_clean_architect_brief_for_pov_and_terraform() -> None:
    context = {
        "customer_name": "ACME Corp",
        "archie": {
            "engagement_summary": "Retail modernization on OCI with private OKE and ADB.",
            "latest_notes_summary": "Need an executive draft first.",
            "latest_approved_constraints": {},
            "latest_approved_assumptions": [],
            "open_questions": [],
            "resolved_questions": [],
            "change_history": [],
            "update_batches": [],
            "pending_update": None,
        },
        "latest_decision_context": {},
    }
    decision_context = {
        "goal": "Draft a POV and scoped Terraform follow-up.",
        "assumption_mode": True,
        "risk_level": "medium",
        "constraints": {"region": "us-phoenix-1"},
        "assumptions": [
            {
                "id": "region_default",
                "statement": "Use the Phoenix region for the first draft.",
                "risk": "medium",
            }
        ],
        "success_criteria": ["Explain customer outcomes clearly."],
        "missing_inputs": ["approved Terraform state backend"],
        "requires_user_confirmation": False,
    }

    pov_args = orchestrator_agent._hydrate_tool_args_from_context(
        tool_name="generate_pov",
        args={"feedback": "Draft the POV.\n\n[Decision Context]\nsecret\n[End Decision Context]"},
        context=context,
        decision_context=decision_context,
        user_message="Draft the POV.",
    )
    tf_args = orchestrator_agent._hydrate_tool_args_from_context(
        tool_name="generate_terraform",
        args={"prompt": "Create Terraform.\n\n[Injected Skill Guidance]\nsecret\n[End Skill Guidance]"},
        context=context,
        decision_context=decision_context,
        user_message="Create Terraform.",
    )

    assert pov_args["feedback"] == "Draft the POV."
    assert pov_args["_architect_brief"]["user_notes"] == "Draft the POV."
    assert "Retail modernization on OCI" in pov_args["_architect_brief"]["architect_context"]
    assert tf_args["prompt"] == "Create Terraform."
    assert tf_args["_architect_brief"]["missing_inputs"] == ["approved Terraform state backend"]


def test_generate_pov_from_sparse_notes_with_context_produces_draft(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "pov-sparse", "POV Sparse")
    context_store.set_archie_engagement_summary(
        ctx,
        "Retail customer modernizing ecommerce to private OKE with WAF and Autonomous Database.",
    )
    context_store.write_context(store, "pov-sparse", ctx)

    async def _fake_execute_tool_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_pov"
        assert args["_architect_brief"]["user_notes"] == "Draft a POV from these rough notes."
        return ("POV v1 saved. Key: pov/pov-sparse/v1.md", "pov/pov-sparse/v1.md", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_tool_core)
    monkeypatch.setattr(orchestrator_agent, "_build_context_summary_for_skills", lambda *_a, **_k: "notes exist")
    monkeypatch.setattr(
        orchestrator_agent.critic_agent,
        "evaluate_tool_result",
        lambda **_kwargs: {
            "overall_status": "pass",
            "security": {"status": "pass", "findings": [], "required_actions": []},
            "cost": {"status": "pass", "estimated_monthly_cost": None, "budget_target": None, "variance": None, "findings": []},
            "quality": {"status": "pass", "issues": [], "suggestions": [], "confidence": 90, "summary": "Acceptable", "severity": "low"},
            "decision_summary": "POV draft accepted.",
            "reason_codes": [],
            "overall_pass": True,
            "confidence": 90,
            "issues": [],
            "suggestions": [],
            "critique_summary": "Acceptable",
            "severity": "low",
        },
    )

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {"feedback": "Draft a POV from these rough notes."},
            customer_id="pov-sparse",
            customer_name="POV Sparse",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Draft a POV from these rough notes.",
        )
    )

    assert "POV v1 saved" in summary
    assert key == "pov/pov-sparse/v1.md"
    assert "archie_question_bundle" not in data


def test_generate_pov_without_enough_context_returns_targeted_questions() -> None:
    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_pov",
            {"feedback": "Draft a POV from rough notes."},
            customer_id="pov-questions",
            customer_name="POV Questions",
            store=InMemoryObjectStore(),
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Draft a POV from rough notes.",
        )
    )

    assert "Archie needs confirmation on the remaining specialist inputs" in summary
    assert "pov.business_outcomes" in summary
    assert data["archie_question_bundle"]["type"] == "specialist_questions"


def test_generate_terraform_with_architecture_but_sparse_constraints_returns_targeted_questions() -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "tf-sparse", "TF Sparse")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/tf-sparse/v1/diagram.drawio",
            "node_count": 6,
            "deployment_summary": "single_ad, 6 nodes",
        },
    )
    context_store.write_context(store, "tf-sparse", ctx)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_terraform",
            {"prompt": "Draft Terraform for the current architecture."},
            customer_id="tf-sparse",
            customer_name="TF Sparse",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="langgraph",
            user_message="Draft Terraform for the current architecture.",
        )
    )

    assert "Archie needs confirmation on the remaining specialist inputs" in summary
    assert "terraform.module_scope" in summary
    assert "terraform.state_backend" in summary
    assert data["archie_question_bundle"]["type"] == "specialist_questions"


def test_record_tool_state_persists_diagram_and_bom_agent_context() -> None:
    store = InMemoryObjectStore()

    orchestrator_agent._record_tool_decision_state(
        store=store,
        customer_id="acme",
        customer_name="ACME Corp",
        tool_name="generate_diagram",
        artifact_key="diagrams/acme/oci_architecture/v1/diagram.drawio",
        decision_context={
            "goal": "Build an OKE architecture diagram.",
            "constraints": {},
            "assumptions": [
                {
                    "id": "default_region",
                    "statement": "Assume the primary tenancy region.",
                    "risk": "medium",
                }
            ],
            "success_criteria": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        },
        result_data={
            "render_manifest": {"node_count": 6},
            "spec": {"deployment_type": "single_ad"},
            "reference_family": "single_region_oke_app",
            "reference_mode": "matched",
            "assumptions_used": [],
            "governor": {},
        },
    )
    orchestrator_agent._record_tool_decision_state(
        store=store,
        customer_id="acme",
        customer_name="ACME Corp",
        tool_name="generate_bom",
        artifact_key="",
        decision_context={
            "goal": "Build a BOM from the latest diagram.",
            "constraints": {},
            "assumptions": [],
            "success_criteria": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        },
        result_data={
            "type": "final",
            "reply": "Final BOM prepared. Review line items, then export JSON or XLSX.",
            "trace_id": "trace-ctx",
            "trace": {"bom_context_source": "latest_diagram"},
            "bom_payload": {
                "line_items": [{"sku": "B94176"}],
                "assumptions": ["Ballpark defaults applied."],
                "totals": {"estimated_monthly_cost": 640.0},
            },
            "governor": {},
        },
    )

    ctx = context_store.read_context(store, "acme", "ACME Corp")
    assert ctx["agents"]["diagram"]["diagram_key"].endswith("diagram.drawio")
    assert ctx["agents"]["diagram"]["reference_family"] == "single_region_oke_app"
    assert ctx["agents"]["bom"]["estimated_monthly_cost"] == 640.0
    assert ctx["agents"]["bom"]["payload_ref"] == "trace:trace-ctx"

    summary = context_store.build_context_summary(ctx)
    assert "Architecture Diagram" in summary
    assert "reference=single_region_oke_app" in summary
    assert "BOM (v1)" in summary
    assert "estimated_monthly_cost=640.0" in summary


def test_run_turn_architecture_chat_only_does_not_force_artifact_generation(monkeypatch) -> None:
    called = {"count": 0}

    async def _fake_execute_tool(*_args, **_kwargs):
        called["count"] += 1
        return ("unexpected", "", {})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        return '{"tool":"generate_diagram","args":{"bom_text":"Create a diagram"}}'

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="arch-chat",
            customer_name="Arch Chat",
            user_message="Talk me through architecture tradeoffs for a private OKE platform.",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert called["count"] == 0
    assert "architecture discussion first" in result["reply"]


def test_save_notes_then_bom_request_hydrates_archie_context(monkeypatch) -> None:
    store = InMemoryObjectStore()

    asyncio.run(
        orchestrator_agent._execute_tool(
            "save_notes",
            {
                "text": (
                    "Customer wants a private OKE platform in us-phoenix-1 with WAF, "
                    "Autonomous Database, and Object Storage."
                )
            },
            customer_id="notes-bom",
            customer_name="Notes BOM",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Save these notes.",
        )
    )

    fake_service = _FakeBomService(
        [
            {
                "type": "final",
                "reply": "Review line items, then export JSON or XLSX.",
                "trace_id": "trace-notes",
                "trace": {"cache_ready": True, "cache_source": "fallback"},
                "bom_payload": {
                    "line_items": [{"sku": "B94176"}],
                    "assumptions": [],
                    "totals": {"estimated_monthly_cost": 999.0},
                },
            }
        ]
    )
    monkeypatch.setattr(orchestrator_agent, "get_shared_bom_service", lambda: fake_service)

    summary, _key, _data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "Create a BOM from the saved notes."},
            customer_id="notes-bom",
            customer_name="Notes BOM",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Create a BOM from the saved notes.",
        )
    )

    assert summary.startswith("Final BOM prepared.")
    assert "private OKE platform" in fake_service.chat_messages[0]
    assert "Autonomous Database" in fake_service.chat_messages[0]
    ctx = context_store.read_context(store, "notes-bom", "Notes BOM")
    assert "private OKE platform" in ctx["archie"]["engagement_summary"]


def test_save_notes_then_diagram_request_hydrates_archie_context(monkeypatch) -> None:
    store = InMemoryObjectStore()

    asyncio.run(
        orchestrator_agent._execute_tool(
            "save_notes",
            {
                "text": (
                    "Need a multi-region OCI web application with public ingress, WAF, "
                    "OKE, and PostgreSQL."
                )
            },
            customer_id="notes-diagram",
            customer_name="Notes Diagram",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Save these notes.",
        )
    )

    captured: list[dict] = []

    async def _fake_post(*, payload, a2a_base_url):
        _ = a2a_base_url
        captured.append(payload)
        return {
            "task_id": "task-1",
            "status": "ok",
            "outputs": {"object_key": "diagrams/notes-diagram/v1/diagram.drawio"},
        }

    monkeypatch.setattr(orchestrator_agent, "_post_diagram_a2a_task", _fake_post)

    summary, key, _data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Create a diagram from the saved notes."},
            customer_id="notes-diagram",
            customer_name="Notes Diagram",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Create a diagram from the saved notes.",
        )
    )

    assert summary == "Diagram generated. Key: diagrams/notes-diagram/v1/diagram.drawio"
    assert key == "diagrams/notes-diagram/v1/diagram.drawio"
    assert "multi-region OCI web application" in captured[0]["inputs"]["context"]
    assert "PostgreSQL" in captured[0]["inputs"]["context"]


def test_archie_auto_fills_specialist_question_from_stored_context(monkeypatch) -> None:
    store = InMemoryObjectStore()
    asyncio.run(
        orchestrator_agent._execute_tool(
            "save_notes",
            {"text": "Use private-only ingress for the OCI application."},
            customer_id="archie-auto",
            customer_name="Archie Auto",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Save these notes.",
        )
    )

    calls: list[dict] = []

    async def _fake_post(*, payload, a2a_base_url):
        _ = a2a_base_url
        calls.append(payload)
        if len(calls) == 1:
            return {
                "task_id": "task-clarify",
                "status": "need_clarification",
                "outputs": {
                    "questions": [
                        {
                            "id": "network.exposure",
                            "question": "Should ingress be public, private, or both?",
                            "blocking": True,
                        }
                    ]
                },
            }
        return {
            "task_id": "task-final",
            "status": "ok",
            "outputs": {"object_key": "diagrams/archie-auto/v1/diagram.drawio"},
        }

    monkeypatch.setattr(orchestrator_agent, "_post_diagram_a2a_task", _fake_post)

    summary, key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Create the OCI diagram."},
            customer_id="archie-auto",
            customer_name="Archie Auto",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Create the OCI diagram.",
        )
    )

    assert summary == "Diagram generated. Key: diagrams/archie-auto/v1/diagram.drawio"
    assert key == "diagrams/archie-auto/v1/diagram.drawio"
    assert len(calls) == 2
    assert "network.exposure: private" in calls[1]["inputs"]["notes"]
    ctx = context_store.read_context(store, "archie-auto", "Archie Auto")
    assert ctx["archie"]["resolved_questions"][-1]["question_id"] == "network.exposure"
    assert data["archie_auto_answers"][0]["final_answer"] == "private"


def test_archie_returns_single_question_batch_with_suggestions(monkeypatch) -> None:
    store = InMemoryObjectStore()
    asyncio.run(
        orchestrator_agent._execute_tool(
            "save_notes",
            {"text": "This is a private OCI workload."},
            customer_id="archie-batch",
            customer_name="Archie Batch",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Save these notes.",
        )
    )

    async def _fake_post(*, payload, a2a_base_url):
        _ = (payload, a2a_base_url)
        return {
            "task_id": "task-batch",
            "status": "need_clarification",
            "outputs": {
                "questions": [
                    {
                        "id": "network.exposure",
                        "question": "Should ingress be public, private, or both?",
                        "blocking": True,
                    },
                    {
                        "id": "workload.components",
                        "question": "What major OCI components need to appear in the diagram?",
                        "blocking": True,
                    },
                ]
            },
        }

    monkeypatch.setattr(orchestrator_agent, "_post_diagram_a2a_task", _fake_post)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_diagram",
            {"bom_text": "Create the OCI diagram."},
            customer_id="archie-batch",
            customer_name="Archie Batch",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Create the OCI diagram.",
        )
    )

    assert "Archie needs confirmation on the remaining specialist inputs" in summary
    assert "Question ID: network.exposure" in summary
    assert "Suggested answer: private" in summary
    assert "Question ID: workload.components" in summary
    assert data["archie_question_bundle"]["type"] == "specialist_questions"
    ctx = context_store.read_context(store, "archie-batch", "Archie Batch")
    assert ctx["pending_checkpoint"]["type"] == "specialist_questions"


def test_pending_specialist_answers_are_recorded_before_rerun(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "archie-rerun", "Archie Rerun")
    context_store.record_resolved_question(
        ctx,
        {
            "id": "old-network",
            "question_id": "network.exposure",
            "question": "Should ingress be public, private, or both?",
            "final_answer": "public",
        },
    )
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "pending-specialist",
            "type": "specialist_questions",
            "status": "pending",
            "tool_name": "generate_diagram",
            "tool_args": {"bom_text": "Create the OCI diagram."},
            "original_request": "Create the OCI diagram.",
            "questions": [
                {
                    "question_id": "network.exposure",
                    "question": "Should ingress be public, private, or both?",
                    "suggested_answer": "private",
                    "confidence": "medium",
                },
                {
                    "question_id": "workload.components",
                    "question": "What major OCI components need to appear in the diagram?",
                },
            ],
            "prompt": "pending specialist questions",
        },
    )
    context_store.write_context(store, "archie-rerun", ctx)

    captured = {}

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        captured["tool_name"] = tool_name
        captured["args"] = dict(args)
        refreshed = context_store.read_context(store, "archie-rerun", "Archie Rerun")
        latest = refreshed["archie"]["resolved_questions"][-1]
        captured["latest_answer"] = latest["final_answer"]
        return (
            "Diagram generated. Key: diagrams/archie-rerun/v2/diagram.drawio",
            "diagrams/archie-rerun/v2/diagram.drawio",
            {},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-rerun",
            customer_name="Archie Rerun",
            user_message="network.exposure: private\nworkload.components: OKE, Load Balancer, Database",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Diagram generated. Key: diagrams/archie-rerun/v2/diagram.drawio"
    assert captured["tool_name"] == "generate_diagram"
    assert "network.exposure: private" in captured["args"]["bom_text"]
    assert captured["latest_answer"] == "OKE, Load Balancer, Database"
    updated = context_store.read_context(store, "archie-rerun", "Archie Rerun")
    assert updated["pending_checkpoint"] is None
    answers = updated["archie"]["resolved_questions"]
    assert answers[-2]["supersedes"] == "old-network"
    assert answers[-1]["final_answer"] == "OKE, Load Balancer, Database"


def test_pending_specialist_answers_accept_loose_id_separators(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "archie-loose", "Archie Loose")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "pending-specialist",
            "type": "specialist_questions",
            "status": "pending",
            "tool_name": "generate_diagram",
            "tool_args": {"bom_text": "Create diagrams."},
            "original_request": "Create diagrams.",
            "questions": [
                {
                    "question_id": "components.scope",
                    "question": "What major OCI components should be shown?",
                },
                {
                    "question_id": "regions.mode",
                    "question": "Should I assume single-region, multi-AD, or multi-region?",
                },
            ],
            "prompt": "pending specialist questions",
        },
    )
    context_store.write_context(store, "archie-loose", ctx)
    captured = {}

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        captured["tool_name"] = tool_name
        captured["args"] = dict(args)
        return (
            "Diagram generated. Key: diagrams/archie-loose/v2/diagram.drawio",
            "diagrams/archie-loose/v2/diagram.drawio",
            {},
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-loose",
            customer_name="Archie Loose",
            user_message="Components.scope. all\nregion.mode, single ad",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Diagram generated. Key: diagrams/archie-loose/v2/diagram.drawio"
    assert captured["tool_name"] == "generate_diagram"
    assert "components.scope: all" in captured["args"]["bom_text"]
    assert "regions.mode: single ad" in captured["args"]["bom_text"]
    updated = context_store.read_context(store, "archie-loose", "Archie Loose")
    assert updated["pending_checkpoint"] is None


def test_recall_request_returns_persisted_context_without_regeneration() -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "archie-recall", "Archie Recall")
    context_store.set_archie_engagement_summary(ctx, "Private OKE platform with WAF and BOM draft.")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 2,
            "diagram_key": "diagrams/archie-recall/v2/diagram.drawio",
            "node_count": 7,
            "deployment_summary": "single_ad, 7 nodes",
        },
    )
    context_store.write_context(store, "archie-recall", ctx)

    llm_calls = {"count": 0}

    def _text_runner(_prompt: str, _system_message: str) -> str:
        llm_calls["count"] += 1
        return "unused"

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-recall",
            customer_name="Archie Recall",
            user_message="What did we have before?",
            store=store,
            text_runner=_text_runner,
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "latest persisted Archie engagement state" in result["reply"]
    assert "Private OKE platform with WAF and BOM draft." in result["reply"]
    assert "Architecture Diagram (v2)" in result["reply"]
    assert "Management Summary" not in result["reply"]
    assert llm_calls["count"] == 0


def test_update_plan_and_confirmation_track_superseded_decisions(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "archie-update", "Archie Update")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/archie-update/v1/diagram.drawio",
            "node_count": 6,
            "deployment_summary": "single_ad, 6 nodes",
        },
    )
    context_store.record_agent_run(ctx, "waf", [], {"version": 1, "overall_rating": "good", "key": "waf/v1.md"})
    context_store.record_agent_run(ctx, "terraform", [], {"version": 1, "file_count": 4, "prefix_key": "tf/v1"})
    context_store.record_resolved_question(
        ctx,
        {
            "id": "network-old",
            "question_id": "network.exposure",
            "question": "Should ingress be public, private, or both?",
            "final_answer": "public",
        },
    )
    context_store.write_context(store, "archie-update", ctx)

    plan_result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-update",
            customer_name="Archie Update",
            user_message="We learned the architecture must use private-only ingress.",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "These outputs are impacted" in plan_result["reply"]
    assert "generate_diagram" in plan_result["reply"]
    assert "generate_waf" in plan_result["reply"]
    assert "generate_terraform" in plan_result["reply"]

    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        calls.append(tool_name)
        return (f"{tool_name} updated", f"{tool_name}/key", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    confirm_result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-update",
            customer_name="Archie Update",
            user_message="confirm update all",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert calls == ["generate_diagram", "generate_waf", "generate_terraform"]
    assert "approved update sequence" in confirm_result["reply"]
    assert "Management Summary" in confirm_result["reply"]
    assert "Checkpoint status: none" in confirm_result["reply"]
    updated = context_store.read_context(store, "archie-update", "Archie Update")
    assert updated["archie"]["pending_update"] is None
    assert updated["archie"]["change_history"][-1]["status"] == "applied"
    assert updated["archie"]["change_history"][-1]["superseded_decision_ids"] == ["network-old"]

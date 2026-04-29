from __future__ import annotations

import asyncio

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent import context_store
from agent import document_store
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


def test_checkpoint_from_result_skips_bom_cost_checkpoint_without_budget_target() -> None:
    checkpoint = orchestrator_agent._checkpoint_from_result(
        tool_name="generate_bom",
        decision_context={
            "goal": "Generate a BOM and XLSX.",
            "constraints": {"region": "us-ashburn-1", "cost_max_monthly": None},
            "assumptions": [],
            "missing_inputs": [],
            "requires_user_confirmation": False,
        },
        result_data={
            "governor": {
                "overall_status": "checkpoint_required",
                "decision_summary": "Cost checkpoint requested without a budget target.",
                "cost": {
                    "status": "checkpoint_required",
                    "estimated_monthly_cost": 2083.09,
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


def test_note_capture_only_does_not_generate_bom_and_followup_uses_notes(monkeypatch) -> None:
    store = InMemoryObjectStore()

    first = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="context-bom",
            customer_name="Context BOM",
            user_message=(
                "Customer notes: the customer asked us to estimate OCI monthly cost and produce a BOM/XLSX "
                "for a workload requiring 48 OCPU, 768 GB RAM, and 42 TB block storage in us-ashburn-1. "
                "Do not build it yet; just remember these notes."
            ),
            store=store,
            text_runner=lambda _prompt, _system: "should not be used",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "saved those customer notes" in first["reply"]
    assert first["tool_calls"] == []

    recall = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="context-bom",
            customer_name="Context BOM",
            user_message="What did the customer ask for?",
            store=store,
            text_runner=lambda _prompt, _system: "should not be used",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert "48 OCPU" in recall["reply"]
    assert "768 GB RAM" in recall["reply"]
    assert "42 TB block storage" in recall["reply"]

    fake_service = _FakeBomService(
        [
            {
                "type": "final",
                "reply": "Review line items, then export JSON or XLSX.",
                "trace_id": "trace-context",
                "trace": {"cache_ready": True, "cache_source": "fallback"},
                "bom_payload": {
                    "line_items": [
                        {"sku": "B94176", "description": "Compute E4 OCPU", "category": "compute", "quantity": 48},
                        {"sku": "B94177", "description": "Compute E4 Memory GB", "category": "compute", "quantity": 768},
                        {"sku": "B91961", "description": "Block Volume Capacity GB", "category": "storage", "quantity": 43008},
                    ],
                    "assumptions": [],
                    "totals": {"estimated_monthly_cost": 1859.424},
                },
            }
        ]
    )
    monkeypatch.setattr(orchestrator_agent, "get_shared_bom_service", lambda: fake_service)

    final = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="context-bom",
            customer_name="Context BOM",
            user_message="Use that information from the notes and conversation to create the BOM and XLSX now.",
            store=store,
            text_runner=lambda _prompt, _system: '{"overall_status":"pass"}',
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    data = final["tool_calls"][0]["result_data"]

    assert final["reply"].startswith("Final BOM prepared.")
    assert "48 OCPU" in fake_service.chat_messages[0]
    assert "768 GB RAM" in fake_service.chat_messages[0]
    assert "42 TB block storage" in fake_service.chat_messages[0]
    assert data["trace"]["bom_context_source"] == "persisted_notes"
    assert data["trace"]["review_verdict"] == "pass"


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
    assert "components.scope: all BOM-derived and standard reference architecture components" in captured["args"]["bom_text"]
    assert "regions.mode: single ad" in captured["args"]["bom_text"]
    updated = context_store.read_context(store, "archie-loose", "Archie Loose")
    assert updated["pending_checkpoint"] is None


def test_pending_specialist_retry_recovers_prior_loose_answers(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "global-small-site", "Global Small Site")
    prompt = "pending specialist questions"
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "pending-specialist",
            "type": "specialist_questions",
            "status": "pending",
            "tool_name": "generate_bom",
            "tool_args": {"prompt": "Create a BOM for Global Small Site."},
            "original_request": "Create a BOM for Global Small Site.",
            "questions": [
                {
                    "question_id": "components.scope",
                    "question": "What major OCI components should be included?",
                },
                {
                    "question_id": "regions.mode",
                    "question": "Should I assume single-region, multi-AD, or multi-region?",
                },
            ],
            "prompt": prompt,
        },
    )
    context_store.set_open_questions(ctx, ctx["pending_checkpoint"]["questions"])
    context_store.write_context(store, "global-small-site", ctx)
    document_store.save_conversation_turns(
        store,
        "global-small-site",
        [
            {"role": "assistant", "content": prompt, "timestamp": "2026-04-29T00:00:00Z"},
            {
                "role": "user",
                "content": "Components.scope. all\nregion.mode, single ad",
                "timestamp": "2026-04-29T00:01:00Z",
            },
        ],
    )
    captured = {}

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        captured["tool_name"] = tool_name
        captured["args"] = dict(args)
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "bom_payload": {
                    "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                    "totals": {"estimated_monthly_cost": 500},
                },
            },
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="global-small-site",
            customer_name="Global Small Site",
            user_message="Try again",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Final BOM prepared. Review line items."
    assert captured["tool_name"] == "generate_bom"
    assert "components.scope: all BOM-derived and standard reference architecture components" in captured["args"]["prompt"]
    assert "regions.mode: single ad" in captured["args"]["prompt"]
    updated = context_store.read_context(store, "global-small-site", "Global Small Site")
    assert updated["pending_checkpoint"] is None
    assert updated["archie"]["open_questions"] == []
    assert updated["archie"]["resolved_questions"][-2]["final_answer"].startswith(
        "all BOM-derived and standard reference architecture components"
    )
    assert updated["archie"]["resolved_questions"][-1]["final_answer"] == "single ad"


def test_pending_specialist_loose_answer_ids_do_not_supersede_checkpoint(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "archie-loose-supersede", "Archie Loose")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "pending-specialist",
            "type": "specialist_questions",
            "status": "pending",
            "tool_name": "generate_bom",
            "tool_args": {"prompt": "Create BOM."},
            "original_request": "Create BOM.",
            "questions": [
                {"question_id": "components.scope", "question": "What components?"},
                {"question_id": "regions.mode", "question": "What region mode?"},
            ],
            "prompt": "pending specialist questions",
        },
    )
    context_store.write_context(store, "archie-loose-supersede", ctx)
    captured = {}

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        captured["tool_name"] = tool_name
        captured["args"] = dict(args)
        return ("Final BOM prepared.", "", {"type": "final", "bom_payload": {"line_items": []}})

    def _text_runner(_prompt: str, _system_message: str) -> str:
        raise AssertionError("Loose specialist answers should handle the pending checkpoint directly")

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="archie-loose-supersede",
            customer_name="Archie Loose",
            user_message="Generate BOM.\nComponents.scope. all\nregion.mode, single ad",
            store=store,
            text_runner=_text_runner,
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert result["reply"] == "Final BOM prepared."
    assert captured["tool_name"] == "generate_bom"
    assert "components.scope: all BOM-derived and standard reference architecture components" in captured["args"]["prompt"]


def test_south_africa_region_intent_is_normalized_and_reused() -> None:
    ctx = context_store.read_context(InMemoryObjectStore(), "za-region", "ZA Region")
    for phrase in ("South Africa", "Johannesburg", "south aferica"):
        decision = orchestrator_agent.decision_context_builder.build_decision_context(
            user_message=f"Generate a diagram in {phrase}.",
            context=ctx,
        )
        assert decision["constraints"]["region"] == "af-johannesburg-1"
        assert "preferred OCI region" not in decision["missing_inputs"]

    context_store.set_archie_decision_state(
        ctx,
        constraints={"region": "af-johannesburg-1"},
        assumptions=[],
    )
    followup = orchestrator_agent.decision_context_builder.build_decision_context(
        user_message="Now generate the BOM.",
        context=ctx,
    )
    assert followup["constraints"]["region"] == "af-johannesburg-1"
    assert "preferred OCI region" not in followup["missing_inputs"]


def test_components_scope_and_region_aliases_auto_fill_from_context() -> None:
    ctx = context_store.read_context(InMemoryObjectStore(), "scope-alias", "Global Small Site")
    context_store.set_archie_engagement_summary(
        ctx,
        "Global Small Site standard reference architecture with a prior BOM draft.",
    )
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {"version": 1, "result_type": "final", "line_item_count": 4, "summary": "BOM draft"},
    )
    context_store.record_resolved_question(
        ctx,
        {
            "id": "region-mode-1",
            "question_id": "region.mode",
            "question": "Regional topology",
            "final_answer": "single ad",
        },
    )

    component_answer, component_basis, component_confidence = orchestrator_agent._suggest_answer_for_question(
        {"question_id": "components.scope", "question": "What major OCI components should be shown?"},
        context=ctx,
        user_message="Use all components.",
    )
    region_answer, _region_basis, region_confidence = orchestrator_agent._suggest_answer_for_question(
        {"question_id": "regions.mode", "question": "Should I assume single-region, multi-AD, or multi-region?"},
        context=ctx,
        user_message="Create the diagram.",
    )

    assert component_answer.startswith("all BOM-derived and standard reference architecture components")
    assert "BOM" in component_basis
    assert component_confidence == "high"
    assert region_answer == "single ad"
    assert region_confidence == "high"


def test_missing_bom_sizing_leaves_one_targeted_question(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "sizing-only", "Global Small Site")
    context_store.set_archie_engagement_summary(ctx, "Global Small Site in South Africa with standard components.")
    context_store.set_archie_decision_state(ctx, constraints={"region": "af-johannesburg-1"}, assumptions=[])
    context_store.write_context(store, "sizing-only", ctx)

    async def _fake_execute_core(*_args, **_kwargs):
        return (
            "BOM clarification required.",
            "",
            {
                "type": "question",
                "questions": [
                    {"id": "components.scope", "question": "What major OCI components should be included?"},
                    {"id": "regions.mode", "question": "Single-region, multi-AD, or multi-region?"},
                    {"id": "workload.sizing", "question": "What OCPU, memory, storage, and quantity sizing should I use?"},
                ],
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "Now I need the BOM."},
            customer_id="sizing-only",
            customer_name="Global Small Site",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Now I need the BOM.",
            decision_context=orchestrator_agent.decision_context_builder.build_decision_context(
                user_message="Now I need the BOM.",
                context=ctx,
            ),
        )
    )

    assert "Question ID: workload.sizing" in summary
    assert "Question ID: components.scope" in summary
    assert "Suggested answer: all BOM-derived and standard reference architecture components" in summary
    assert data["archie_question_bundle"]["type"] == "specialist_questions"
    pending = context_store.read_context(store, "sizing-only", "Global Small Site")["pending_checkpoint"]
    unresolved = [item for item in pending["questions"] if not item.get("final_answer")]
    assert [item["question_id"] for item in unresolved] == ["workload.sizing"]


def test_bom_clarification_auto_answers_retry_and_exposes_resolved_inputs(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "bom-auto-answer", "BOM Auto Answer")
    context_store.write_context(store, "bom-auto-answer", ctx)
    calls: list[dict] = []

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        if not args.get("_archie_question_retry"):
            return (
                "BOM clarification required.",
                "",
                {
                    "type": "question",
                    "questions": [
                        "Before finalizing, please confirm target region.",
                        "Should this use GPU or non-GPU compute?",
                        "How many OCPU should be included?",
                        "How much memory should be included?",
                        "How much block storage should be included?",
                        "What Block Volume VPU setting should be used?",
                        "Should a load balancer be included?",
                        "Should Object Storage be included?",
                        "What connectivity should be included?",
                        "What monthly budget cap should I use?",
                    ],
                },
            )
        assert "[Archie Resolved Specialist Inputs]" in args["prompt"]
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 48}],
                    "totals": {"estimated_monthly_cost": 4500},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="bom-auto-answer",
            customer_name="BOM Auto Answer",
            user_message=(
                "Generate a BOM in us-ashburn-1 for non-GPU compute: 48 OCPU, 768 GB RAM, "
                "42 TB block storage, Balanced 10 VPU/GB, load balancer, Object Storage, "
                "FastConnect, under $5000 monthly."
            ),
            store=store,
            text_runner=lambda _prompt, _system: (
                '{"tool": "generate_bom", "args": {"prompt": "Generate a BOM in us-ashburn-1 for non-GPU compute: '
                '48 OCPU, 768 GB RAM, 42 TB block storage, Balanced 10 VPU/GB, load balancer, Object Storage, '
                'FastConnect, under $5000 monthly."}}'
            ),
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert len(calls) == 2
    assert "constraints.region: us-ashburn-1" in calls[1]["prompt"]
    assert "bom.compute.ocpu: 48 OCPU" in calls[1]["prompt"]
    assert "bom.compute.memory: 768 GB RAM" in calls[1]["prompt"]
    assert "bom.storage.block: 42 TB block storage" in calls[1]["prompt"]
    assert "bom.network.connectivity: FastConnect connectivity" in calls[1]["prompt"]
    assert "Archie used these answers:" in result["reply"]
    assert "- bom.compute.ocpu: 48 OCPU" in result["reply"]
    tool_data = result["tool_calls"][0]["result_data"]
    resolved_inputs = tool_data["bom_payload"]["resolved_inputs"]
    assert len(resolved_inputs) == 9
    assert {item["question_id"] for item in resolved_inputs} >= {
        "constraints.region",
        "bom.compute.gpu",
        "bom.compute.ocpu",
        "bom.compute.memory",
        "bom.storage.block",
        "bom.storage.vpu",
        "bom.network.load_balancer",
        "bom.storage.object",
        "bom.network.connectivity",
    }
    assert {item["question_id"] for item in resolved_inputs}.isdisjoint({"bom.budget"})


def test_chat_discovery_profile_hydrates_followup_bom(monkeypatch) -> None:
    store = InMemoryObjectStore()
    calls: list[dict] = []

    discovery = (
        "RGA discovery notes: platform VMware ESXi on VxRail. CPU: 96 logical cores, "
        "4 sockets, 24 cores per socket, processor model Intel Xeon Gold 6248R, "
        "used 1200 GHz, total 2400 GHz. Memory used: 768 GB total: 1024 GB. "
        "Storage used: 42 TB total: 100 TB. Connectivity: internet bandwidth 200 Mbps, "
        "MPLS and SD-WAN. DR: cross-region restore with 24h SLA. Workloads include "
        "2 DCs, SQL DBs, Oracle DBs, custom apps, patch repo, and file servers."
    )

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        if not args.get("_archie_question_retry"):
            return (
                "BOM clarification required.",
                "",
                {
                    "type": "question",
                    "questions": [
                        "Before finalizing, please confirm target region.",
                        "Should this use GPU or non-GPU compute?",
                        "How many OCPU should be included?",
                        "How much memory should be included?",
                        "How much block storage should be included?",
                        "What connectivity should be included?",
                    ],
                },
            )
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [{"sku": "B94176", "description": "Compute OCPU", "quantity": 96}],
                    "totals": {"estimated_monthly_cost": 9000},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)

    first = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="rga-profile",
            customer_name="RGA Profile",
            user_message=discovery,
            store=store,
            text_runner=lambda _prompt, _system: "Captured the discovery details.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert first["tool_calls"] == []

    ctx = context_store.read_context(store, "rga-profile", "RGA Profile")
    profile = ctx["archie"]["infrastructure_profile"]
    assert profile["platform"] == "VxRail / VMware ESXi"
    assert profile["cpu"]["logical_cores"] == 96
    assert profile["cpu"]["sockets"] == 4
    assert profile["cpu"]["cores_per_socket"] == 24
    assert profile["memory"]["used_gb"] == 768
    assert profile["storage"]["used_tb"] == 42
    assert profile["connectivity"]["internet_bandwidth"] == "200 Mbps"
    assert profile["connectivity"]["mpls"] is True
    assert profile["connectivity"]["sd_wan"] is True
    assert profile["dr"]["cross_region_restore"] is True
    assert profile["dr"]["sla_hours"] == 24

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="rga-profile",
            customer_name="RGA Profile",
            user_message="lets assume VMware and build BOM",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]
    assert "Archie needs confirmation" not in result["reply"]
    updated = context_store.read_context(store, "rga-profile", "RGA Profile")
    assert updated["pending_checkpoint"] is None
    assert "preferred OCI region" not in updated["latest_decision_context"]["missing_inputs"]
    assert any(
        item["id"] == "bom_region_pricing_consistent"
        for item in updated["latest_decision_context"]["assumptions"]
    )
    assert len(calls) == 2
    retry_prompt = calls[1]["prompt"]
    assert "VxRail / VMware ESXi" in retry_prompt
    assert "logical cores=96" in retry_prompt
    assert "memory: used_gb=768" in retry_prompt
    assert "storage: used_tb=42" in retry_prompt
    assert "internet bandwidth 200 Mbps" in retry_prompt
    assert "SD-WAN" in retry_prompt
    assert "sla_hours=24" in retry_prompt
    assert "bom.compute.gpu: non-GPU compute" in retry_prompt
    assert "bom.compute.ocpu: 96 OCPU equivalent" in retry_prompt
    assert "bom.compute.memory: 1024 GB RAM" in retry_prompt
    assert "bom.storage.block: 100 TB block storage" in retry_prompt
    assert "pricing-only estimate; treat OCI pricing as region-consistent" in retry_prompt


def test_discovery_facts_allow_later_xlsx_request_to_generate_bom(monkeypatch) -> None:
    store = InMemoryObjectStore()
    calls: list[dict] = []
    discovery = (
        "KR1 discovery: South Africa customer on VxRail VMware ESXi. "
        "96 logical cores, memory used: 768 GB total: 1024 GB, storage used: 42 TB total: 100 TB. "
        "100Mbps internet, MPLS and SD-WAN, WAF and bastion required, single AD, 24h DR. "
        "Workloads include domain controllers, SQL DBs, Oracle DBs, custom apps, patch repo, and file servers."
    )

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [{"sku": "B94176", "description": "Compute OCPU", "quantity": 96}],
                    "totals": {"estimated_monthly_cost": 9000},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)

    first = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="kr1-xlsx",
            customer_name="KR1",
            user_message=discovery,
            store=store,
            text_runner=lambda _prompt, _system: "Captured.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert first["tool_calls"] == []

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="kr1-xlsx",
            customer_name="KR1",
            user_message="I need the XLSX now",
            store=store,
            text_runner=lambda _prompt, _system: "should not plan",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]
    assert calls
    prompt = calls[0]["prompt"]
    assert "accumulated_client_facts" in prompt
    assert "VxRail / VMware ESXi" in prompt
    assert "af-johannesburg-1" in prompt
    assert "waf=True" in prompt or '"waf": true' in prompt
    assert "budget" not in result["reply"].lower()


def test_kr1_bom_request_injects_canonical_memory_without_sizing_clarification(monkeypatch) -> None:
    store = InMemoryObjectStore()
    calls: list[dict] = []
    discovery = (
        "KR1 discovery: VxRail VMware preference in South Africa. "
        "Workloads: SQL Server, Oracle databases, Linux servers, Windows servers. "
        "96 logical cores, memory used 768 GB total 1024 GB, storage used 42 TB total 100 TB. "
        "100Mbps internet, MPLS, SD-WAN, cross-region restore, 24hr SLA."
    )

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        assert isinstance(args.get("_memory_snapshot"), dict)
        assert args.get("_memory_snapshot_hash")
        assert "[Archie Canonical Memory]" in args["prompt"]
        assert "Use the provided memory as the source of truth" in args["prompt"]
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [
                        {"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 96},
                        {"sku": "B88514", "description": "Compute memory", "category": "compute", "quantity": 1024},
                        {"sku": "B91961", "description": "Block storage", "category": "storage", "quantity": 102400},
                    ],
                    "totals": {"estimated_monthly_cost": 9000},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )

    first = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="kr1-memory",
            customer_name="KR1",
            user_message=discovery,
            store=store,
            text_runner=lambda _prompt, _system: "Captured.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert first["tool_calls"] == []

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="kr1-memory",
            customer_name="KR1",
            user_message="we only need a BOM and estimated cost to start",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]
    assert "Archie needs confirmation" not in result["reply"]
    assert "Facts Used from Memory" in result["reply"]
    memory = calls[0]["_memory_snapshot"]
    assert memory["client_facts"]["platform"] == "VxRail / VMware"
    assert memory["client_facts"]["sizing"]["memory"]["total_gb"] == 1024
    assert "SQL Server" in memory["client_facts"]["databases"]
    assert "Linux" in memory["client_facts"]["os_mix"]
    trace = result["tool_calls"][0]["result_data"]["trace"]
    assert trace["memory_snapshot_hash"] == calls[0]["_memory_snapshot_hash"]
    assert "client_facts" in trace["memory_sections_injected"]


def test_specialist_memory_contract_applies_to_all_generation_tools(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "memory-all-tools", "Memory All Tools")
    context_store.merge_archie_client_facts(
        ctx,
        {
            "region": "us-ashburn-1",
            "platform": "VMware",
            "workloads": ["web", "database"],
            "infrastructure": {"cpu": {"logical_cores": 8}, "memory": {"total_gb": 128}, "storage": {"total_tb": 4}},
            "connectivity": {"vpn": True},
            "security": {"waf": True},
        },
    )
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/memory-all-tools/v1/diagram.drawio",
            "summary": "VCN, public load balancer, app subnet, database subnet, WAF.",
            "node_count": 5,
        },
    )
    context_store.write_context(store, "memory-all-tools", ctx)
    captured: dict[str, dict] = {}

    async def _fake_execute_core(tool_name, args, **_kwargs):
        captured[tool_name] = dict(args)
        return (
            f"{tool_name} completed",
            "artifact-key" if tool_name != "generate_bom" else "",
            {
                "type": "final",
                "reply": f"{tool_name} completed",
                "bom_payload": {"line_items": [{"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 8}], "totals": {}}
                if tool_name == "generate_bom"
                else {},
                "render_manifest": {"node_count": 5} if tool_name == "generate_diagram" else {},
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_skill_preflight_for_tool", lambda **_kwargs: None)
    monkeypatch.setattr(
        orchestrator_agent._SKILL_ENGINE,
        "postflight_check",
        lambda **kwargs: orchestrator_agent.OrchestratorSkillDecision(
            path_id=kwargs.get("path_id", ""),
            phase="postflight",
            status="allow",
            reasons=[],
            pushback_message="",
            retry_instructions=[],
        ),
    )

    requests = {
        "generate_bom": {"prompt": "Generate BOM"},
        "generate_diagram": {"bom_text": "Generate diagram"},
        "generate_waf": {"feedback": "Review WAF"},
        "generate_terraform": {"prompt": "Generate networking module with Object Storage remote state and private NSG security controls"},
        "generate_pov": {"feedback": "Draft POV for business modernization and private OKE architecture"},
        "generate_jep": {"feedback": "Draft JEP from current engagement context"},
    }
    for tool_name, args in requests.items():
        asyncio.run(
            orchestrator_agent._execute_tool(
                tool_name,
                args,
                customer_id="memory-all-tools",
                customer_name="Memory All Tools",
                store=store,
                text_runner=lambda _prompt, _system: '{"overall_status":"pass","security":{"status":"pass","findings":[],"required_actions":[]},"cost":{"status":"pass","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":[]},"quality":{"status":"pass","issues":[],"suggestions":[],"confidence":95,"summary":"ok","severity":"low"},"decision_summary":"ok","reason_codes":[]}',
                a2a_base_url="http://localhost:8080",
                specialist_mode="legacy",
                user_message=args.get("prompt") or args.get("feedback") or args.get("bom_text") or "",
                decision_context={"goal": "test", "constraints": {"region": "us-ashburn-1"}, "assumptions": [], "missing_inputs": []},
            )
        )

    for tool_name, args in captured.items():
        primary = orchestrator_agent._tool_primary_input_key(tool_name)
        assert isinstance(args.get("_memory_snapshot"), dict), tool_name
        assert args.get("_memory_snapshot_hash"), tool_name
        assert primary and "[Archie Canonical Memory]" in args[primary], tool_name


def test_new_xlsx_with_incorrect_prior_bom_builds_revision_brief(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "revision-bom", "Revision BOM")
    context_store.merge_archie_client_facts(
        ctx,
        {
            "geography": "South Africa",
            "platform": "VxRail / VMware ESXi",
            "security": {"waf": True, "bastion": True},
            "connectivity": {"mpls": True, "sd_wan": True},
            "dr": {"sla_hours": 24},
        },
    )
    prior_payload = {
        "currency": "USD",
        "line_items": [
            {"sku": "B94176", "description": "Generic compute", "category": "compute", "quantity": 4}
        ],
        "totals": {"estimated_monthly_cost": 500},
    }
    context_store.record_bom_work_product(
        ctx,
        bom_payload=prior_payload,
        context_source="direct_request",
        grounding="generic",
    )
    bom_key = "customers/revision-bom/bom/xlsx/old.xlsx"
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put(
        f"{bom_key}.metadata.json",
        b'{"tool":"generate_bom","status":"approved","checkpoint_required":false}',
        "application/json",
    )
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 1,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "old.xlsx",
            "bom_xlsx": {"key": bom_key, "filename": "old.xlsx"},
        },
    )
    context_store.write_context(store, "revision-bom", ctx)
    calls: list[dict] = []

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [
                        {"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 96},
                        {"sku": "WAF", "description": "Web Application Firewall", "category": "security", "quantity": 1},
                    ],
                    "totals": {"estimated_monthly_cost": 9500},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="revision-bom",
            customer_name="Revision BOM",
            user_message="The previous BOM is incorrect. Build a new XLSX version with the new data.",
            store=store,
            text_runner=lambda _prompt, _system: "should not return old link",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]
    assert "/api/bom/revision-bom/download/old.xlsx" not in result["reply"]
    prompt = calls[0]["prompt"]
    assert "Revise the current BOM/XLSX work product" in prompt
    assert "[Prior BOM Baseline]" in prompt
    assert "WAF is required" in prompt
    assert "bastion is required" in prompt
    assert "SD-WAN connectivity" in prompt


def test_bom_feedback_with_object_storage_mentions_regenerates_instead_of_verifying(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "bom-feedback", "BOM Feedback")
    context_store.merge_archie_client_facts(
        ctx,
        {
            "geography": "South Africa",
            "platform": "VxRail / VMware ESXi",
            "infrastructure": {
                "cpu": {"logical_cores": 64},
                "memory": {"total_gb": 1146.88},
                "storage": {"total_tb": 44},
            },
        },
    )
    context_store.record_bom_work_product(
        ctx,
        bom_payload={
            "line_items": [
                {"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 64},
                {"sku": "B88514", "description": "Compute memory", "category": "compute", "quantity": 64},
                {"sku": "B91628", "description": "Object storage", "category": "storage", "quantity": 204},
            ],
            "totals": {"estimated_monthly_cost": 1000},
        },
        context_source="direct_request",
        grounding="generic",
    )
    bom_key = "customers/bom-feedback/bom/xlsx/old.xlsx"
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put(
        f"{bom_key}.metadata.json",
        b'{"tool":"generate_bom","status":"approved","checkpoint_required":false}',
        "application/json",
    )
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 1,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "old.xlsx",
            "bom_xlsx": {"key": bom_key, "filename": "old.xlsx"},
        },
    )
    context_store.write_context(store, "bom-feedback", ctx)
    calls: list[dict] = []

    async def _fake_execute_core(tool_name, args, **_kwargs):
        assert tool_name == "generate_bom"
        calls.append(dict(args))
        return (
            "Final BOM prepared. Review line items.",
            "",
            {
                "type": "final",
                "reply": "Final BOM prepared. Review line items.",
                "bom_payload": {
                    "line_items": [
                        {"sku": "B94176", "description": "Compute OCPU", "category": "compute", "quantity": 64},
                        {"sku": "B88514", "description": "Compute memory", "category": "compute", "quantity": 1433.6},
                        {"sku": "B91961", "description": "Block storage", "category": "storage", "quantity": 47104},
                    ],
                    "totals": {"estimated_monthly_cost": 2000},
                },
            },
        )

    async def _no_critic(**kwargs):
        return kwargs["result_summary"], kwargs["artifact_key"], kwargs["result_data"]

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)
    monkeypatch.setattr(orchestrator_agent, "_critic_refine_if_needed", _no_critic)
    monkeypatch.setattr(orchestrator_agent, "_archie_expert_review_if_needed", _no_critic)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="bom-feedback",
            customer_name="BOM Feedback",
            user_message=(
                "Feed back on the BOM, the customer asked for 46TB of storage, you only have "
                "204GB of object storage. They had 1.4TB of memory in their pool but you have 64GB."
            ),
            store=store,
            text_runner=lambda _prompt, _system: "should not verify artifacts",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert [call["tool"] for call in result["tool_calls"]] == ["generate_bom"]
    assert "Artifact verification from persisted object-store state" not in result["reply"]
    assert "BOM revision was performed from updated memory" in result["reply"]
    prompt = calls[0]["prompt"]
    assert "Revise the current BOM/XLSX work product" in prompt
    assert "[Corrected Facts From Current Turn]" in prompt
    assert "46" in prompt and "storage" in prompt.lower()
    assert "1433.6" in prompt or "1.4TB" in prompt
    updated = context_store.read_context(store, "bom-feedback", "BOM Feedback")
    sizing = updated["archie"]["memory"]["client_facts"]["sizing"]
    assert sizing["storage"]["total_tb"] == 46
    assert sizing["memory"]["total_gb"] == 1433.6


def test_bom_mixed_confidence_keeps_unresolved_checkpoint(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "bom-mixed-confidence", "BOM Mixed")
    context_store.write_context(store, "bom-mixed-confidence", ctx)

    async def _fake_execute_core(*_args, **_kwargs):
        return (
            "BOM clarification required.",
            "",
            {
                "type": "question",
                "questions": [
                    "How many OCPU should be included?",
                    "How much memory should be included?",
                    "What database license model should be used?",
                ],
            },
        )

    monkeypatch.setattr(orchestrator_agent, "_execute_tool_core", _fake_execute_core)

    summary, _key, data = asyncio.run(
        orchestrator_agent._execute_tool(
            "generate_bom",
            {"prompt": "Generate BOM for 8 OCPU and 128 GB RAM."},
            customer_id="bom-mixed-confidence",
            customer_name="BOM Mixed",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            a2a_base_url="http://localhost:8080",
            specialist_mode="legacy",
            user_message="Generate BOM for 8 OCPU and 128 GB RAM.",
            decision_context=orchestrator_agent.decision_context_builder.build_decision_context(
                user_message="Generate BOM for 8 OCPU and 128 GB RAM.",
                context=ctx,
            ),
        )
    )

    assert "Question ID: bom.compute.ocpu" in summary
    assert "Suggested answer: 8 OCPU" in summary
    assert "Question ID: bom.compute.memory" in summary
    assert "Suggested answer: 128 GB RAM" in summary
    assert "Question ID: generate_bom.q3" in summary
    pending_questions = data["archie_question_bundle"]["questions"]
    unresolved = [item for item in pending_questions if not item.get("final_answer")]
    assert [item["question_id"] for item in unresolved] == ["generate_bom.q3"]


def test_approved_checkpoint_inputs_are_reused_for_followup_bom(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "checkpoint-bom", "Checkpoint BOM")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "checkpoint-1",
            "type": "assumption_review",
            "status": "pending",
            "tool_name": "generate_bom",
            "prompt": "Discovery checkpoint required before final acceptance.",
            "decision_context_hash": "hash",
            "decision_context": {
                "constraints": {"region": "af-johannesburg-1"},
                "assumptions": [
                    {
                        "id": "small_site_components",
                        "statement": "Use the standard small-site architecture components.",
                        "risk": "medium",
                    },
                    {
                        "id": "single_region",
                        "statement": "Use a single-region deployment.",
                        "risk": "medium",
                    },
                ],
            },
            "constraints": {"region": "af-johannesburg-1"},
            "assumptions": [
                {
                    "id": "small_site_components",
                    "statement": "Use the standard small-site architecture components.",
                    "risk": "medium",
                },
                {
                    "id": "single_region",
                    "statement": "Use a single-region deployment.",
                    "risk": "medium",
                },
            ],
        },
    )
    context_store.write_context(store, "checkpoint-bom", ctx)

    approve = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="checkpoint-bom",
            customer_name="Checkpoint BOM",
            user_message="approve checkpoint",
            store=store,
            text_runner=lambda _prompt, _system: "unused",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert "Checkpoint approved" in approve["reply"]
    approved_ctx = context_store.read_context(store, "checkpoint-bom", "Checkpoint BOM")
    assert approved_ctx["pending_checkpoint"] is None
    assert approved_ctx["archie"]["latest_approved_constraints"]["region"] == "af-johannesburg-1"
    assert any(item["question_id"] == "components.scope" for item in approved_ctx["archie"]["resolved_questions"])

    captured = {}

    async def _fake_bom_request(*, args, **_kwargs):
        captured["prompt"] = args["prompt"]
        return {
            "type": "final",
            "reply": "Review line items.",
            "bom_payload": {
                "line_items": [{"sku": "B94176", "description": "Compute", "quantity": 2}],
                "totals": {"estimated_monthly_cost": 500},
            },
            "trace": {},
        }

    monkeypatch.setattr(orchestrator_agent, "_execute_bom_tool_request", _fake_bom_request)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="checkpoint-bom",
            customer_name="Checkpoint BOM",
            user_message="Now I need the BOM.",
            store=store,
            text_runner=lambda _prompt, _system: '{"tool": "generate_bom", "args": {"prompt": "Now I need the BOM."}}',
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "Final BOM prepared" in result["reply"]
    assert "af-johannesburg-1" in captured["prompt"]
    assert "all BOM-derived and standard reference architecture components" in captured["prompt"]


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


def test_export_xlsx_without_manifest_returns_no_artifact_blocker() -> None:
    llm_calls = {"count": 0}

    def _text_runner(_prompt: str, _system_message: str) -> str:
        llm_calls["count"] += 1
        return "I exported the workbook."

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="export-empty",
            customer_name="Export Empty",
            user_message="export the BOM to xlsx",
            store=InMemoryObjectStore(),
            text_runner=_text_runner,
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "I don't have a generated artifact/link for that yet" in result["reply"]
    assert result["tool_calls"] == []
    assert llm_calls["count"] == 0


def test_workbook_only_followup_does_not_generate_default_bom(monkeypatch) -> None:
    store = InMemoryObjectStore()
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        calls.append(tool_name)
        return ("default BOM should not run", "", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="workbook-only",
            customer_name="Workbook Only",
            user_message="build the xlsc",
            store=store,
            text_runner=lambda _prompt, _system: "should not call planner",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "I don't have a generated artifact/link for that yet" in result["reply"]
    assert result["tool_calls"] == []
    assert calls == []


def test_share_link_returns_only_manifest_backed_downloads() -> None:
    store = InMemoryObjectStore()
    diagram_key = "agent3/link-customer/oci_architecture/v1/diagram.drawio"
    bom_key = "customers/link-customer/bom/xlsx/oci-bom-test.xlsx"
    bom_metadata = {
        "schema_version": "1.0",
        "tool": "generate_bom",
        "status": "approved",
        "checkpoint_required": False,
        "filename": "oci-bom-test.xlsx",
        "key": bom_key,
    }
    store.put(diagram_key, b"<mxGraphModel />", "text/xml")
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put(f"{bom_key}.metadata.json", b'{"tool":"generate_bom","status":"approved","checkpoint_required":false}', "application/json")
    ctx = context_store.read_context(store, "link-customer", "Link Customer")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": diagram_key,
            "diagram_name": "oci_architecture",
            "node_count": 4,
        },
    )
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 1,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "oci-bom-test.xlsx",
            "xlsx_metadata": bom_metadata,
            "bom_xlsx": {"key": bom_key, "filename": "oci-bom-test.xlsx", "metadata": bom_metadata},
        },
    )
    context_store.write_context(store, "link-customer", ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="link-customer",
            customer_name="Link Customer",
            user_message="share link for the generated files",
            store=store,
            text_runner=lambda _prompt, _system: "should not be used",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "/api/download/diagram.drawio?client_id=link-customer&diagram_name=oci_architecture" in result["reply"]
    assert "/api/bom/link-customer/download/oci-bom-test.xlsx" in result["reply"]
    assert "should not be used" not in result["reply"]


def test_download_latest_bom_xlsx_returns_link_without_regeneration(monkeypatch) -> None:
    store = InMemoryObjectStore()
    customer_id = "download-bom"
    bom_key = f"customers/{customer_id}/bom/xlsx/latest.xlsx"
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put(
        f"{bom_key}.metadata.json",
        b'{"tool":"generate_bom","status":"approved","checkpoint_required":false}',
        "application/json",
    )
    ctx = context_store.read_context(store, customer_id, "Download BOM")
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 3,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "latest.xlsx",
            "bom_xlsx": {"key": bom_key, "filename": "latest.xlsx"},
        },
    )
    context_store.write_context(store, customer_id, ctx)

    async def _should_not_execute(*_args, **_kwargs):
        raise AssertionError("download request should not regenerate the BOM")

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _should_not_execute)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id=customer_id,
            customer_name="Download BOM",
            user_message="download the latest BOM XLSX",
            store=store,
            text_runner=lambda _prompt, _system: "should not be used",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "/api/bom/download-bom/download/latest.xlsx" in result["reply"]
    assert result["tool_calls"] == []
    assert "should not be used" not in result["reply"]


def test_verify_bom_xlsx_exists_in_object_storage_runs_verification() -> None:
    store = InMemoryObjectStore()
    customer_id = "verify-bom"
    bom_key = f"customers/{customer_id}/bom/xlsx/latest.xlsx"
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    store.put(
        f"{bom_key}.metadata.json",
        b'{"tool":"generate_bom","status":"approved","checkpoint_required":false}',
        "application/json",
    )
    ctx = context_store.read_context(store, customer_id, "Verify BOM")
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 1,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "latest.xlsx",
            "bom_xlsx": {"key": bom_key, "filename": "latest.xlsx"},
        },
    )
    context_store.write_context(store, customer_id, ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id=customer_id,
            customer_name="Verify BOM",
            user_message="verify the BOM XLSX exists in object storage",
            store=store,
            text_runner=lambda _prompt, _system: "should not regenerate",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "Artifact verification from persisted object-store state" in result["reply"]
    assert f"- bom: present ({bom_key})" in result["reply"]
    assert result["tool_calls"] == []
    assert "should not regenerate" not in result["reply"]


def test_turn_intent_does_not_treat_generic_object_storage_as_verification() -> None:
    intent = orchestrator_agent._classify_turn_intent(
        user_message="Include Object Storage for archive data in the target architecture.",
        requested_tools=set(),
        context={},
    )

    action = orchestrator_agent._tool_backed_action_intent(
        "Include Object Storage for archive data in the target architecture.",
        turn_intent=intent,
    )

    assert intent.classification == "conversation_only"
    assert action == {}


def test_metadata_less_bom_xlsx_is_hidden_from_link_replies() -> None:
    store = InMemoryObjectStore()
    bom_key = "customers/old-bom/bom/xlsx/old.xlsx"
    store.put(bom_key, b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    ctx = context_store.read_context(store, "old-bom", "Old BOM")
    context_store.record_agent_run(
        ctx,
        "bom",
        [],
        {
            "version": 1,
            "result_type": "final",
            "xlsx_artifact_key": bom_key,
            "xlsx_filename": "old.xlsx",
            "bom_xlsx": {"key": bom_key, "filename": "old.xlsx"},
        },
    )
    context_store.write_context(store, "old-bom", ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="old-bom",
            customer_name="Old BOM",
            user_message="share link for the xlsx",
            store=store,
            text_runner=lambda _prompt, _system: "I found old.xlsx",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "I don't have a generated artifact/link for that yet" in result["reply"]
    assert "old.xlsx" not in result["reply"]


def test_bucket_verification_uses_store_head_not_chat_history() -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "verify-customer", "Verify Customer")
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "agent3/verify-customer/oci_architecture/v1/diagram.drawio",
            "node_count": 4,
        },
    )
    context_store.write_context(store, "verify-customer", ctx)
    document_store.save_conversation_turns(
        store,
        "verify-customer",
        [{"role": "assistant", "content": "The file was uploaded successfully."}],
    )

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="verify-customer",
            customer_name="Verify Customer",
            user_message="are these files in the bucket?",
            store=store,
            text_runner=lambda _prompt, _system: "yes, uploaded",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "not found" in result["reply"]
    assert "persisted keys only" in result["reply"]
    assert result["tool_calls"] == []


def test_pending_assumption_checkpoint_blocks_export_until_approval() -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "checkpoint-export", "Checkpoint Export")
    context_store.set_pending_checkpoint(
        ctx,
        {
            "id": "checkpoint-1",
            "type": "assumption_review",
            "status": "pending",
            "tool_name": "generate_bom",
            "prompt": "Discovery checkpoint required before final acceptance.",
            "decision_context_hash": "hash",
        },
    )
    context_store.write_context(store, "checkpoint-export", ctx)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="checkpoint-export",
            customer_name="Checkpoint Export",
            user_message="export xlsx",
            store=store,
            text_runner=lambda _prompt, _system: "exported",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "while this checkpoint is pending" in result["reply"]
    assert "approve checkpoint" in result["reply"]
    assert result["tool_calls"] == []


def test_operating_model_is_advisory_but_export_is_blocked() -> None:
    store = InMemoryObjectStore()
    advisory = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="om-customer",
            customer_name="OM Customer",
            user_message="OM for lift and shift",
            store=store,
            text_runner=lambda _prompt, _system: "Use a migration factory operating model with workstream owners.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert "migration factory operating model" in advisory["reply"]

    export = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="om-customer",
            customer_name="OM Customer",
            user_message="export OM XLSX for lift and shift",
            store=store,
            text_runner=lambda _prompt, _system: "I created the operating model workbook.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )
    assert "Operating Model export is not a supported Archie artifact path yet" in export["reply"]
    assert "I created the operating model workbook" not in export["reply"]


def test_key_specs_diagram_request_after_reset_routes_to_diagram(monkeypatch) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "rga", "RGA")
    ctx["latest_decision_context"] = {"goal": "export operating model"}
    ctx["pending_checkpoint"] = {"id": "old-checkpoint"}
    context_store.set_archie_engagement_summary(ctx, "Old Operating Model export workflow.")
    context_store.write_context(store, "rga", ctx)
    context_store.reset_context(store, "rga")
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        calls.append(tool_name)
        return (f"{tool_name} completed", "diagrams/rga/v1/diagram.drawio", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="rga",
            customer_name="RGA",
            user_message=(
                "Build a diagram for this Key Specs: us-ashburn-1, hub and spoke VCN, "
                "private OKE, Autonomous Database, and Object Storage."
            ),
            store=store,
            text_runner=lambda _prompt, _system: "I created an Operating Model export.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert calls == ["generate_diagram"]
    assert [call["tool"] for call in result["tool_calls"]] == ["generate_diagram"]
    assert "Operating Model export is not a supported Archie artifact path yet" not in result["reply"]


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("build me the xlxs bom", "generate_bom"),
        ("generate a draw.io topology file", "generate_diagram"),
        ("generate terraform for the latest diagram with Resource Manager remote state and private networking", "generate_terraform"),
        ("generate a POV for the workshop", "generate_pov"),
        ("generate a JEP for the workshop", "generate_jep"),
        ("run a WAF review for the latest architecture", "generate_waf"),
    ],
)
def test_deliverable_requests_invoke_only_matching_specialist(monkeypatch, message, expected_tool) -> None:
    store = InMemoryObjectStore()
    ctx = context_store.read_context(store, "specialist-only", "Specialist Only")
    context_store.set_archie_engagement_summary(
        ctx,
        "Retail customer migrating ecommerce to private OKE with WAF and Autonomous Database.",
    )
    context_store.record_agent_run(
        ctx,
        "diagram",
        [],
        {
            "version": 1,
            "diagram_key": "diagrams/specialist-only/v1/diagram.drawio",
            "node_count": 6,
            "deployment_summary": "private OKE architecture",
        },
    )
    context_store.write_context(store, "specialist-only", ctx)
    calls: list[str] = []

    async def _fake_execute_tool(tool_name, args, **_kwargs):
        calls.append(tool_name)
        return (f"{tool_name} completed", f"{tool_name}/artifact" if tool_name != "generate_bom" else "", {})

    monkeypatch.setattr(orchestrator_agent, "_execute_tool", _fake_execute_tool)
    monkeypatch.setattr(orchestrator_agent, "_terraform_scope_details_are_bounded", lambda **_kwargs: True)

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="specialist-only",
            customer_name="Specialist Only",
            user_message=message,
            store=store,
            text_runner=lambda _prompt, _system: "I free-wrote the deliverable.",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert calls == [expected_tool]
    assert [call["tool"] for call in result["tool_calls"]] == [expected_tool]
    assert "I free-wrote the deliverable" not in result["reply"]


def test_migration_target_question_uses_persisted_context_only() -> None:
    llm_calls = {"count": 0}

    result = asyncio.run(
        orchestrator_agent.run_turn(
            customer_id="no-target",
            customer_name="No Target",
            user_message="what system are we migrating?",
            store=InMemoryObjectStore(),
            text_runner=lambda _prompt, _system: llm_calls.__setitem__("count", llm_calls["count"] + 1) or "Invented ERP",
            max_tool_iterations=1,
            specialist_mode="legacy",
        )
    )

    assert "I don't have a verified migration target recorded" in result["reply"]
    assert "Invented ERP" not in result["reply"]
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

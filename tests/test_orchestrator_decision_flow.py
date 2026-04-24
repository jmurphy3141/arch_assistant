from __future__ import annotations

import asyncio

import pytest

import agent.orchestrator_agent as orchestrator_agent
from agent import context_store
from agent.persistence_objectstore import InMemoryObjectStore


pytestmark = pytest.mark.integration


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


def test_checkpoint_from_result_uses_discovery_prompt_when_cost_data_missing() -> None:
    checkpoint = orchestrator_agent._checkpoint_from_result(
        tool_name="generate_bom",
        decision_context={
            "goal": "Need a ballpark BOM and diagram from rough notes.",
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
            "requires_user_confirmation": True,
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

    assert checkpoint is not None
    assert checkpoint["type"] == "assumption_review"
    assert "Discovery checkpoint required before final acceptance." in checkpoint["prompt"]
    assert "Assumptions applied:" in checkpoint["prompt"]
    assert "Estimated monthly cost: None" not in checkpoint["prompt"]

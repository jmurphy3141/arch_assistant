import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.archie_wiring import build_forge


def _long_pre_action_response() -> str:
    return "\n".join(
        [
            "KNOWN FACTS: The customer is asking for infrastructure research before choosing a final OCI design. "
            "The workload context is intentionally concise, so the research agent should compare viable OCI "
            "patterns and preserve assumptions for later BOM and diagram work.",
            "GAPS: Exact transaction rate, regulatory controls, recovery objectives, and data gravity are not "
            "confirmed. The specialist should call those out as assumptions rather than inventing precise sizing.",
            "EXPERT ASSESSMENT: A technology research pass is appropriate before BOM, diagram, POV, JEP, WAF, "
            "or Terraform generation because the customer needs option comparison and service mapping first. "
            "The report should include at least two patterns, pros and cons, recommended OCI services, risks, "
            "and downstream sizing hints that can feed the BOM and diagram tools without committing to unverified "
            "capacity values.",
            "SUB-AGENT TASK: Generate a structured OCI infrastructure research report with workload pattern, "
            "architecture options, service mapping, tradeoffs, assumptions, risks, and sizing hints. Return a "
            "persisted report artifact key so Forge can track the output for downstream specialist handoff.",
        ]
    )


def _long_approved_review() -> str:
    body = "\n".join(
        [
            "PHASE A - Quality Bar check:",
            "PASS: The report result has a persisted artifact key and a usable summary for the customer.",
            "PASS: The research output is appropriate for an early architecture option comparison.",
            "PASS: The result can feed downstream BOM and diagram work through the memory contract.",
            "PHASE B - Post-Action Review checklist:",
            "PASS: artifact_key is present and points to the saved research document.",
            "PASS: status is ok, so the specialist completed without needing more user input.",
            "PASS: the report content is suitable for OCI service mapping and architecture option comparison.",
            "PHASE C - Memory consistency check:",
            "CONSISTENT: There is no confirmed memory value that conflicts with the mocked research result.",
            "PHASE D - Architectural soundness:",
            "GOAL FIT: YES, the generated report directly supports service selection before design commitment.",
            "ANTIPATTERNS: NONE, no implementation design was over-specified in the mocked result.",
            "NEXT STEP FLAG: SUGGEST: use the research artifact as input to BOM and diagram generation.",
        ]
    )
    return body + "\n" + ("PASS: additional review evidence. " * 20) + "\nEXPERT_APPROVED"


def test_generate_tech_report_forge_wiring_and_required_hat_loop():
    store = MagicMock()
    responses = [
        '{"tool": "generate_tech_report", "args": {"feedback": "Compare OCI options for a web workload."}}',
        _long_pre_action_response(),
        _long_approved_review(),
        '{"tool": "critic_approve", "args": {}}',
        "Research report is ready.",
    ]

    async def _text_runner(*_args):
        return responses.pop(0)

    text_runner = MagicMock(side_effect=_text_runner)
    forge = build_forge(
        store=store,
        customer_id="test",
        customer_name="Test",
        text_runner=text_runner,
        step3_planning=False,
    )

    tools = list(forge._registry.names())
    assert "generate_tech_report" in tools

    spec = forge._registry.get("generate_tech_report")
    assert spec.requires_hat == "infra_tech_research"
    assert spec.memory_contract is True
    assert spec.critique_enabled is True

    with patch(
        "agent.tools.specialists.sub_agent_client.call_sub_agent",
        new=AsyncMock(
            return_value={
                "status": "ok",
                "result": "# OCI Technology Research\n\nUse OKE or Compute depending on workload fit.",
                "trace": {"agent": "tech_research"},
            }
        ),
    ), patch(
        "agent.tools.specialists.document_store.save_doc",
        return_value={"key": "research/test/v1.md", "version": 1},
    ):
        result = asyncio.run(
            forge.run_turn(
                session_id="test-session",
                user_message="Research OCI options for a web workload.",
                context={},
            )
        )

    tech_call = next(
        call for call in result.tool_calls if call.tool == "generate_tech_report"
    )
    assert tech_call.result.status == "ok"
    assert tech_call.result.artifact_key == "research/test/v1.md"
    assert result.artifacts["generate_tech_report"] == "research/test/v1.md"
    assert any(
        event.type == "hat_auto_activated"
        and event.data == {
            "hat": "infra_tech_research",
            "tool": "generate_tech_report",
        }
        for event in result.events
    )

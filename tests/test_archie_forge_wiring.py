"""Architecture guards for current deterministic and conversational dispatch."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.archie_wiring import build_forge
from agent.persistence_objectstore import InMemoryObjectStore
from skillforge.protocols import ArgSchema
from skillforge.types import TurnResult


GENERATION_TOOLS = {
    "generate_bom",
    "generate_diagram",
    "generate_terraform",
    "generate_waf",
    "generate_pov",
    "generate_jep",
    "generate_tech_report",
    "generate_poc_plan",
    "generate_presentation",
    "generate_sales_deck",
}

GENERATION_HATS = [
    "oci_bom_expert",
    "diagram_for_oci",
    "terraform_for_oci",
    "oci_waf_reviewer",
    "oci_customer_pov_writer",
    "jep_writer",
    "infra_tech_research",
    "oci_poc_strategist",
    "oci_presentation_writer",
    "oci_sales_deck",
]

INTERNAL_TOOLS = {"save_notes", "get_summary", "get_document"}


def _mock_turn_result(reply: str = "done") -> TurnResult:
    return TurnResult(
        reply=reply,
        tool_calls=[],
        events=[],
        artifacts={},
    )


async def _dummy_text_runner(*_args, **_kwargs) -> str:
    return "done"


def _build_test_forge():
    return build_forge(
        store=InMemoryObjectStore(),
        customer_id="test",
        customer_name="Test User",
        text_runner=_dummy_text_runner,
        step3_planning=False,
    )


@pytest.mark.parametrize(
    ("message", "expected_tool"),
    [
        ("Build a BOM for a web app with 2 servers", "generate_bom"),
        ("Generate a diagram for a 3-tier OCI architecture", "generate_diagram"),
        ("Generate a WAF review on my current architecture", "generate_waf"),
        ("Generate a Terraform plan for my diagram", "generate_terraform"),
        ("Create a POV document", "generate_pov"),
        ("Draft a JEP for this migration", "generate_jep"),
    ],
)
def test_explicit_generation_uses_only_matching_forge_tool(message, expected_tool):
    from agent import archie_session

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=_mock_turn_result())
    from skillforge.types import ToolResult
    mock_forge.invoke_tool = AsyncMock(return_value=ToolResult(summary="done", status="ok"))

    mock_store = MagicMock()
    mock_text_runner = MagicMock()

    with patch("agent.archie_session._get_forge", return_value=mock_forge), \
         patch("agent.archie_session.document_store") as mock_ds, \
         patch("agent.archie_session.context_store") as mock_cs, \
         patch("agent.archie_session.decision_context_builder") as mock_dcb:

        mock_ds.load_conversation_history.return_value = []
        mock_ds.load_conversation_summary.return_value = ""
        mock_ds.save_conversation_turns.return_value = None
        mock_cs.read_context = MagicMock(return_value={})
        mock_cs.write_context.return_value = None
        mock_cs.get_pending_checkpoint.return_value = None
        mock_cs.get_pending_update.return_value = None
        mock_cs.build_context_summary.return_value = ""
        mock_dcb.build_decision_context.return_value = {}

        asyncio.run(archie_session.run_turn(
            customer_id="test",
            customer_name="Test User",
            user_message=message,
            store=mock_store,
            text_runner=mock_text_runner,
        ))

    mock_forge.run_turn.assert_not_called()
    assert [call.args[0] for call in mock_forge.invoke_tool.await_args_list] == [expected_tool]


def test_conversational_architecture_request_uses_reasoning_loop():
    from agent import archie_session

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=_mock_turn_result("Tradeoff discussion"))
    mock_forge.invoke_tool = AsyncMock(side_effect=AssertionError("advice must not generate"))

    with patch("agent.archie_session._get_forge", return_value=mock_forge):
        asyncio.run(archie_session.run_turn(
            customer_id="routing-conversation",
            customer_name="Test User",
            user_message="What are the tradeoffs between VPN and FastConnect?",
            store=InMemoryObjectStore(),
            text_runner=MagicMock(),
        ))

    mock_forge.run_turn.assert_called_once()
    mock_forge.invoke_tool.assert_not_called()


def test_generation_tools_have_descriptions():
    forge = _build_test_forge()

    for tool_name in GENERATION_TOOLS:
        spec = forge._registry.get(tool_name)
        assert spec is not None
        assert spec.description


def test_generation_tools_have_arg_schemas():
    forge = _build_test_forge()

    for tool_name in GENERATION_TOOLS:
        spec = forge._registry.get(tool_name)
        assert spec is not None
        assert isinstance(spec.args, dict)
        assert spec.args
        assert all(isinstance(arg, ArgSchema) for arg in spec.args.values())


def test_build_tool_schemas_excludes_internal_tools():
    forge = _build_test_forge()

    schemas_without_hats = forge._build_tool_schemas(active_hats=[])
    names_without_hats = {schema.name for schema in schemas_without_hats}
    assert not (names_without_hats & INTERNAL_TOOLS)
    assert not any(
        name.startswith("use_hat_") or name.startswith("drop_hat_")
        for name in names_without_hats
    )

    schemas_with_hats = forge._build_tool_schemas(active_hats=GENERATION_HATS)
    names_with_hats = {schema.name for schema in schemas_with_hats}
    assert not (names_with_hats & INTERNAL_TOOLS)
    assert GENERATION_TOOLS <= names_with_hats

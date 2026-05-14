"""
Architecture guard: assert forge.run_turn() is called for every
generation request. Fails if a bypass block is re-introduced in
archie_session.py.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skillforge.types import ToolResult, TurnResult


def _mock_turn_result(reply: str = "done") -> TurnResult:
    return TurnResult(
        reply=reply,
        tool_calls=[],
        events=[],
        artifacts={},
    )


@pytest.mark.parametrize("message", [
    "I need a BOM for a web app with 2 servers",
    "Generate a diagram for a 3-tier OCI architecture",
    "Run a WAF review on my current architecture",
    "Generate a Terraform plan for my diagram",
    "Write a POV document",
])
def test_forge_run_turn_called_for_generation_requests(message):
    """forge.run_turn() must be called for all generation messages."""
    from agent import archie_session

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=_mock_turn_result())
    mock_forge.invoke_tool = AsyncMock(
        return_value=ToolResult(summary="done", status="ok")
    )

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

    mock_forge.run_turn.assert_called_once(), (
        f"forge.run_turn() was NOT called for message: '{message}'\n"
        "This means a bypass block in archie_session.py is routing "
        "this request directly to a tool, skipping Forge's reasoning loop."
    )

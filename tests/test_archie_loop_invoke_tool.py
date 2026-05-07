import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_prerouting_bom_uses_invoke_tool(monkeypatch):
    """
    A BOM-only request goes through _run_generation_step which should call
    forge.invoke_tool, not _execute_tool.
    """
    import agent.archie_loop as archie_loop
    from skillforge.types import ToolResult

    invoke_calls = []

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock()  # should NOT be called
    mock_forge.invoke_tool = AsyncMock(
        side_effect=lambda tool, args, **kw: (
            invoke_calls.append(tool) or
            ToolResult(summary="BOM done", status="ok", artifact_key="bom/v1.xlsx")
        )
    )

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="Generate a BOM for my workload",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert "generate_bom" in invoke_calls
    mock_forge.run_turn.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_not_called_from_run_turn(monkeypatch):
    """
    _execute_tool should never be called from run_turn() after this migration.
    Any call raises AssertionError so the test fails loudly.
    """
    import agent.archie_loop as archie_loop
    from skillforge.types import TurnResult, ToolResult

    def _fail(*a, **kw):
        raise AssertionError("_execute_tool must not be called from run_turn()")

    monkeypatch.setattr(archie_loop, "_execute_tool", _fail)

    fake_result = TurnResult(reply="Hi!", tool_calls=[], artifacts={}, history_length=1)
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)
    mock_forge.invoke_tool = AsyncMock(
        return_value=ToolResult(summary="done", status="ok")
    )

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        # Both a conversational message and a generation request should avoid _execute_tool
        for msg in ["What can you help me with?", "Generate a BOM"]:
            await archie_loop.run_turn(
                customer_id="c1",
                customer_name="Acme",
                user_message=msg,
                store=MagicMock(),
                text_runner=AsyncMock(return_value="done"),
            )

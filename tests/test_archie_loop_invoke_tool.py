import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_execute_tool_not_called_from_run_turn(monkeypatch):
    """
    _execute_tool should never be called from run_turn() after this migration.
    Any call raises AssertionError so the test fails loudly.
    """
    import agent.archie_session as archie_session
    from skillforge.types import TurnResult, ToolResult

    def _fail(*a, **kw):
        raise AssertionError("_execute_tool must not be called from run_turn()")

    monkeypatch.setattr(archie_session, "_execute_tool", _fail)

    fake_result = TurnResult(reply="Hi!", tool_calls=[], artifacts={}, history_length=1)
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)
    mock_forge.invoke_tool = AsyncMock(
        return_value=ToolResult(summary="done", status="ok")
    )

    monkeypatch.setattr(archie_session, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_session.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_session.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_session.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        # Both a conversational message and a generation request should avoid _execute_tool
        for msg in ["What can you help me with?", "Generate a BOM"]:
            await archie_session.run_turn(
                customer_id="c1",
                customer_name="Acme",
                user_message=msg,
                store=MagicMock(),
                text_runner=AsyncMock(return_value="done"),
            )

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_intent_routing_skill_in_system_prompt():
    """Intent routing guidance is present in Forge's assembled system prompt."""
    from agent.archie_wiring import build_forge

    forge = build_forge(
        store=None,
        customer_id="test",
        customer_name="Test",
        text_runner=AsyncMock(),
        base_system_prompt="You are Archie.",
    )
    system_msg = forge._get_system_msg()
    assert "conversationally" in system_msg or "no tool call" in system_msg.lower(), \
        "Intent routing skill not found in system prompt"


def test_recall_intent_not_hardcoded_in_run_turn():
    """_is_recall_intent should not be called from run_turn after migration."""
    import ast
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_recall_intent" not in source, \
        "_is_recall_intent is still called from run_turn — remove the Python block"


def test_note_capture_not_hardcoded_in_run_turn():
    """_is_note_capture_only_request should not be called from run_turn after migration."""
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_note_capture_only_request" not in source, \
        "_is_note_capture_only_request is still called from run_turn — remove the Python block"


def test_architecture_chat_not_hardcoded_in_run_turn():
    """_is_architecture_chat_only_request should not be called from run_turn."""
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_architecture_chat_only_request" not in source, \
        "_is_architecture_chat_only_request still in run_turn — remove the Python block"


@pytest.mark.asyncio
async def test_run_turn_still_returns_reply_for_conversational_message(monkeypatch):
    """After removing Python blocks, run_turn still handles conversational messages."""
    import agent.archie_loop as archie_loop
    from skillforge.types import TurnResult

    fake_result = TurnResult(reply="Great question!", tool_calls=[], artifacts={}, history_length=1)
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)
    mock_forge.invoke_tool = AsyncMock()

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What is the difference between OCI and AWS?",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert result["reply"] == "Great question!"

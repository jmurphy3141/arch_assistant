import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from skillforge.types import TurnResult, ToolCall, ToolResult


@pytest.mark.asyncio
async def test_run_turn_delegates_to_forge(monkeypatch):
    import agent.archie_session as archie_session

    fake_result = TurnResult(
        reply="Here is the architecture.",
        tool_calls=[],
        artifacts={},
        history_length=2,
    )
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)

    monkeypatch.setattr(archie_session, "_get_forge", lambda *_a, **_kw: mock_forge)
    monkeypatch.setattr(
        archie_session.document_store, "load_conversation_history", lambda *a: []
    )
    monkeypatch.setattr(
        archie_session.document_store, "load_conversation_summary", lambda *a: ""
    )
    monkeypatch.setattr(
        archie_session.document_store, "save_conversation_turns", lambda *a, **kw: None
    )
    monkeypatch.setattr(archie_session.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_session.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What can you help me with?",
            store=MagicMock(),
            text_runner=MagicMock(return_value="done"),
        )

    assert result["reply"] == "Here is the architecture."
    assert result["tool_calls"] == []
    assert result["artifacts"] == {}


@pytest.mark.asyncio
async def test_run_turn_includes_artifacts(monkeypatch):
    import agent.archie_session as archie_session

    tc = ToolCall(
        tool="generate_bom",
        args={},
        result=ToolResult(
            summary="BOM done",
            status="ok",
            artifact_key="bom/v1.xlsx",
        ),
        iteration=0,
    )
    fake_result = TurnResult(
        reply="BOM generated.",
        tool_calls=[tc],
        artifacts={"generate_bom": "bom/v1.xlsx"},
        history_length=3,
    )
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)

    monkeypatch.setattr(archie_session, "_get_forge", lambda *_a, **_kw: mock_forge)
    monkeypatch.setattr(
        archie_session.document_store, "load_conversation_history", lambda *a: []
    )
    monkeypatch.setattr(
        archie_session.document_store, "load_conversation_summary", lambda *a: ""
    )
    monkeypatch.setattr(
        archie_session.document_store, "save_conversation_turns", lambda *a, **kw: None
    )
    monkeypatch.setattr(archie_session.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_session.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What can you help me with?",
            store=MagicMock(),
            text_runner=MagicMock(return_value="done"),
        )

    assert result["artifacts"] == {"generate_bom": "bom/v1.xlsx"}
    assert result["tool_calls"][0]["tool"] == "generate_bom"
    assert result["tool_calls"][0]["artifact_key"] == "bom/v1.xlsx"

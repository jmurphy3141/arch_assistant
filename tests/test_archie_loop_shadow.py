from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_shadow_disabled_does_not_schedule(monkeypatch):
    import agent.archie_loop as archie_loop

    monkeypatch.delenv("SKILLFORGE_SHADOW", raising=False)

    def _fail_create_task(*_args, **_kwargs):
        raise AssertionError("shadow task should not be scheduled")

    monkeypatch.setattr(archie_loop.asyncio, "create_task", _fail_create_task)

    archie_loop._maybe_start_forge_shadow_turn(
        customer_id="c1",
        customer_name="Acme",
        user_message="hello",
        store=MagicMock(),
        text_runner=MagicMock(),
        a2a_base_url="http://localhost:8080",
        context={},
        history=[],
    )


def test_run_turn_shadow_mode_is_fire_and_forget(monkeypatch):
    from skillforge.types import TurnResult
    import agent.archie_loop as archie_loop

    shadow_calls = []
    saved_turns = []

    class FakeForge:
        async def run_turn(self, **kwargs):
            shadow_calls.append(kwargs)
            return TurnResult(
                reply="shadow reply",
                tool_calls=[],
                artifacts={},
                history_length=9,
            )

    monkeypatch.setenv("SKILLFORGE_SHADOW", "1")
    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kwargs: FakeForge())
    monkeypatch.setattr(
        archie_loop.document_store,
        "load_conversation_history",
        lambda *_args: [{"role": "assistant", "content": "prior"}],
    )
    monkeypatch.setattr(
        archie_loop.document_store,
        "load_conversation_summary",
        lambda *_args: "",
    )
    monkeypatch.setattr(
        archie_loop.document_store,
        "save_conversation_turns",
        lambda *_args: saved_turns.extend(_args[-1]),
    )
    monkeypatch.setattr(
        archie_loop.context_store,
        "read_context",
        lambda *_args: {"customer_id": "c1", "archie": {}},
    )
    monkeypatch.setattr(
        archie_loop.context_store,
        "write_context",
        lambda *_args: None,
    )

    def legacy_text_runner(_prompt, _system_message, _model_profile="orchestrator"):
        return "legacy reply"

    async def _run():
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="hello",
            store=MagicMock(),
            text_runner=legacy_text_runner,
        )
        await asyncio.sleep(0)
        return result

    result = asyncio.run(_run())

    assert result == {
        "reply": "legacy reply",
        "tool_calls": [],
        "artifacts": {},
        "history_length": 3,
    }
    assert [turn["role"] for turn in saved_turns] == ["user", "assistant"]
    assert len(shadow_calls) == 1
    assert shadow_calls[0]["session_id"] == "c1"
    assert shadow_calls[0]["user_message"] == "hello"
    assert shadow_calls[0]["context"]["_current_user_message"] == "hello"

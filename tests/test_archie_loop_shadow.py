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


def test_maybe_start_shadow_turn_does_fire_with_env(monkeypatch):
    """When SKILLFORGE_SHADOW=1, _maybe_start_forge_shadow_turn schedules a task."""
    import agent.archie_loop as archie_loop

    scheduled = []

    def _fake_create_task(coro, **_kwargs):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setenv("SKILLFORGE_SHADOW", "1")
    monkeypatch.setattr(archie_loop.asyncio, "create_task", _fake_create_task)

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

    assert len(scheduled) == 1

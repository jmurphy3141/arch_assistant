"""
Compatibility-safe orchestrator adapter for the v1.5 LangGraph migration.

Current behavior:
- Exposes the v1.5 call surface for an orchestrator graph runner.
- Falls back to the existing orchestrator logic to preserve behavior.

Future behavior:
- Replace fallback internals with true LangGraph graph execution.
"""
from __future__ import annotations

from typing import Callable

from agent.persistence_objectstore import ObjectStoreBase
from agent import orchestrator_agent as legacy_orchestrator


async def run_turn(
    *,
    customer_id: str,
    customer_name: str,
    user_message: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    a2a_base_url: str,
    max_tool_iterations: int,
    specialist_mode: str = "langgraph",
) -> dict:
    """
    LangGraph-compatible orchestrator entry point.
    """
    return await legacy_orchestrator.run_turn(
        customer_id=customer_id,
        customer_name=customer_name,
        user_message=user_message,
        store=store,
        text_runner=text_runner,
        a2a_base_url=a2a_base_url,
        max_tool_iterations=max_tool_iterations,
        specialist_mode=specialist_mode,
    )

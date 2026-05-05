"""
agent/orchestrator_agent.py
---------------------------
Compatibility shim for Archie Agent 0.

The conversational loop implementation lives in agent.archie_loop. Keep this
module small so existing imports can continue to resolve while new work targets
archie_loop directly.
"""
from __future__ import annotations

from typing import Any

import agent.archie_loop as archie_loop


run_turn = archie_loop.run_turn
_execute_tool = archie_loop._execute_tool
_execute_tool_core = archie_loop._execute_tool_core


def __getattr__(name: str) -> Any:
    return getattr(archie_loop, name)


__all__ = ["run_turn"]

"""
skillforge/types.py
--------------------
Shared dataclasses that cross module boundaries.
No dependencies on any other skillforge or agent module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemorySnapshot:
    """
    Assembled view of what the orchestrator knows about the current session.
    Passed to every tool handler registered with memory_contract=True.
    Constructed by Memory.assemble(); never mutated after construction.
    """
    session_id: str
    facts: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    prior_artifacts: dict[str, str] = field(default_factory=dict)   # tool_name -> artifact_key
    decision_context: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)               # full context store blob


@dataclass
class ToolResult:
    """
    Returned by every tool handler.
    The orchestrator uses status to decide whether to present, retry, or block.
    """
    summary: str                            # one-sentence human-readable outcome
    status: str                             # "ok" | "needs_input" | "blocked"
    data: dict[str, Any] = field(default_factory=dict)   # raw payload for critic/safety review
    artifact_key: str = ""                  # object-store key if an artifact was produced


@dataclass
class ToolCall:
    """Record of one tool invocation within a turn."""
    tool: str
    args: dict[str, Any]
    result: ToolResult
    iteration: int = 0


@dataclass
class TurnResult:
    """
    Return value of Forge.run_turn().
    reply is the Markdown string to show the user.
    """
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)   # tool_name -> artifact_key
    history_length: int = 0

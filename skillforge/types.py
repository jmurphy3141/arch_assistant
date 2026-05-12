"""
skillforge/types.py
--------------------
Shared dataclasses that cross module boundaries.
No dependencies on any other skillforge or agent module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ParallelToolCall:
    """Declares one tool to be executed as part of a parallel group."""
    tool: str
    args: dict


# Validated status values — handlers must return one of these four strings.
ToolStatus = Literal["ok", "needs_input", "blocked", "parallel"]


@dataclass(frozen=True)
class MemorySnapshot:
    """
    Assembled view of what the orchestrator knows about the current session.
    Passed to every tool handler registered with memory_contract=True.
    Constructed by Memory.assemble(); frozen to prevent handler mutation.

    facts:            accumulated session facts (region, sizing, constraints, etc.)
    constraints:      hard constraints from decision context
    prior_artifacts:  {tool_name: artifact_key} for latest known artifacts
    decision_context: structured decision context dict
    raw:              full context store blob for tools that need direct access
    formatted:        prompt-ready memory text for expert review, if available
    """
    session_id: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    prior_artifacts: dict[str, str] = field(default_factory=dict)
    decision_context: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    formatted: str = ""


@dataclass
class ToolResult:
    """
    Returned by every tool handler.
    The orchestrator uses status to decide whether to present, retry, or block.

    summary:       one-sentence human-readable outcome (always populated)
    status:        "ok" | "needs_input" | "blocked"
    data:          raw payload for critic/safety review
    artifact_key:  object-store key if an artifact was produced (ok only)
    clarification: message to surface to the user when status == "needs_input"
    parallel_tools: tools to execute concurrently when status == "parallel"
    """
    summary: str
    status: ToolStatus
    data: dict[str, Any] = field(default_factory=dict)
    artifact_key: str = ""
    clarification: str = ""
    parallel_tools: list[ParallelToolCall] | None = None


@dataclass
class ToolCall:
    """Record of one tool invocation within a turn."""
    tool: str
    args: dict[str, Any]
    result: ToolResult
    iteration: int = 0


@dataclass
class TurnEvent:
    """Event emitted while processing a turn."""
    type: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    """
    Return value of Forge.run_turn().
    reply is the Markdown string to show the user.
    """
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    history_length: int = 0
    events: list[TurnEvent] = field(default_factory=list)

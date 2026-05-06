"""
skillforge/protocols.py
------------------------
Runtime-checkable Protocol definitions for the three pluggable seams:
  - ToolHandler     what a domain tool must look like
  - Memory          what a memory implementation must provide
  - SafetyChecker   what a deterministic safety check must provide

All three are typing.Protocol so callers get structural compatibility without
inheriting from a base class. Use isinstance(obj, ToolHandler) only if you
decorate the protocol with @runtime_checkable.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from skillforge.types import MemorySnapshot, ToolResult


@runtime_checkable
class ToolHandler(Protocol):
    """
    Contract for every domain tool registered with Forge.register_tool().

    Args:
        args:      dict the LLM emitted in the tool-call JSON
        memory:    assembled session memory (None if memory_contract=False)
        context:   full context store blob (for tools that need raw access)
        trace_id:  unique id for this invocation

    Returns:
        ToolResult with status "ok", "needs_input", or "blocked"

    Rules:
        - Must be an async callable.
        - Must not raise — surface failures as ToolResult(status="blocked").
        - Must not mutate memory or context.
    """
    def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> Awaitable[ToolResult]: ...


@runtime_checkable
class Memory(Protocol):
    """
    Contract for the session memory implementation.

    assemble() is called once per turn before the ReAct loop starts.
    update() is called after each tool returns, so subsequent tool
    calls in the same turn see the refreshed snapshot.
    """
    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot: ...

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the updated context store blob."""
        ...


@runtime_checkable
class SafetyChecker(Protocol):
    """
    Contract for per-tool deterministic safety checks.
    Called after the tool returns, before the result reaches the critic hat.

    Returns (passed, reason). passed=False blocks delivery.
    Must never call an LLM — deterministic only.
    """
    def __call__(
        self,
        tool_name: str,
        result: ToolResult,
    ) -> tuple[bool, str]: ...


# Convenience type alias for text_runner (the LLM call abstraction)
TextRunner = Callable[[str, str, str], str]  # prompt, system_msg, label -> raw text

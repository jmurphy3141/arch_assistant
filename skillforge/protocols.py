"""
skillforge/protocols.py
------------------------
Runtime-checkable Protocol definitions for the four pluggable seams:
  - ToolHandler     what a domain tool must look like
  - Memory          what a memory implementation must provide
  - SafetyChecker   what a deterministic safety check must provide
  - HatEngine       what the hat system module must expose
  - PromptEnricher  optional per-round context injection hook

All are typing.Protocol so callers get structural compatibility without
inheriting from a base class.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from skillforge.types import MemorySnapshot, ToolResult


@runtime_checkable
class ToolHandler(Protocol):
    """
    Contract for every domain tool registered with Forge.register_tool().

    Rules:
        - Must be an async callable.
        - Must not raise — surface failures as ToolResult(status="blocked").
        - Must not mutate memory or context.
        - When input is insufficient, return status="needs_input" and populate
          clarification with the one sentence the user needs to see.
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

    assemble() is called once per turn before the ReAct loop starts, and
    again after each memory_contract tool call to reflect new results.

    update() mutates the context store blob (domain-specific) and returns
    the updated blob. Forge replaces its local context reference with
    the return value — do not mutate in place.
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
    ) -> dict[str, Any]: ...


@runtime_checkable
class SafetyChecker(Protocol):
    """
    Contract for per-tool deterministic safety checks.
    Called after the tool returns ok, before the result reaches the LLM.

    Returns (passed, reason). passed=False blocks delivery and surfaces
    reason to the LLM for a follow-up explanation.
    Must never call an LLM — deterministic only.
    """
    def __call__(
        self,
        tool_name: str,
        result: ToolResult,
    ) -> tuple[bool, str]: ...


@runtime_checkable
class HatEngine(Protocol):
    """
    Structural protocol for the hat engine module (agent/hat_engine.py).
    Defined here so Forge can type-check the duck-typed hat_engine arg.
    """
    def get_hat_tool_definitions(self) -> list[dict]: ...
    def apply_hat(self, active_hats: list[str], hat_name: str) -> list[str]: ...
    def drop_hat(self, active_hats: list[str], hat_name: str) -> list[str]: ...
    def warn_stale_hats(
        self,
        active_hats: list[str],
        rounds_active: dict[str, int],
        max_rounds: int = 5,
    ) -> list[str]: ...
    def inject_hats(self, prompt: str, active_hats: list[str]) -> str: ...


@runtime_checkable
class PromptEnricher(Protocol):
    """
    Optional hook called before each LLM round to inject per-turn context
    (memory summary, decision context, conversation summary, etc.) into
    the prompt. Keeps domain-specific prompt assembly out of Forge core.

    Returns the enriched prompt string.
    """
    def __call__(self, prompt: str, memory: MemorySnapshot) -> str: ...


# The LLM call abstraction — must be async.
# Signature: (prompt, system_message, label) -> raw_text
AsyncTextRunner = Callable[[str, str, str], Awaitable[str]]

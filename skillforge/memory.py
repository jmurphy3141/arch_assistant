"""
skillforge/memory.py
--------------------
SimpleMemory: zero-config in-memory Memory implementation.

Sufficient for new domain teams, testing, and the quickstart example.
Persists nothing across process restarts. Thread-safe within a single
asyncio event loop (no shared state between sessions).
"""
from __future__ import annotations

from typing import Any

from skillforge.types import MemorySnapshot, ToolResult


class _SimpleMemorySnapshot(MemorySnapshot):
    @property
    def artifacts(self) -> dict[str, str]:
        return self.prior_artifacts


class SimpleMemory:
    """
    In-memory Memory implementation. No setup required.

    Stores facts, constraints, and artifacts extracted from ToolResult.data
    between turns. Each session_id has independent state.

    Usage:
        from skillforge import Forge, SimpleMemory
        forge = Forge(..., memory=SimpleMemory())
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot:
        state = self._store.get(session_id, {})
        artifacts = dict(state.get("artifacts") or {})
        snapshot = MemorySnapshot(
            session_id=session_id,
            facts=dict(state.get("facts") or {}),
            constraints=dict(state.get("constraints") or {}),
            prior_artifacts=artifacts,
            raw=state,
        )
        return _SimpleMemorySnapshot(
            session_id=snapshot.session_id,
            facts=snapshot.facts,
            constraints=snapshot.constraints,
            prior_artifacts=snapshot.prior_artifacts,
            decision_context=snapshot.decision_context,
            raw=snapshot.raw,
        )

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        state = self._store.setdefault(session_id, {})
        data = result.data or {}

        # Merge facts if provided
        if "facts" in data and isinstance(data["facts"], dict):
            state.setdefault("facts", {}).update(data["facts"])

        # Merge constraints if provided
        if "constraints" in data and isinstance(data["constraints"], dict):
            state.setdefault("constraints", {}).update(data["constraints"])

        # Track artifact keys
        if result.artifact_key:
            state.setdefault("artifacts", {})[tool_name] = result.artifact_key

        return context

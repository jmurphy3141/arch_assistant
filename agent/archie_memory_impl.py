"""
agent/archie_memory_impl.py
---------------------------
Memory protocol adapter for Archie's context store.
"""
from __future__ import annotations

from typing import Any

from agent.context_store import get_archie_state, write_context
from agent.persistence_objectstore import ObjectStoreBase
from skillforge.types import MemorySnapshot, ToolResult


class ArchieMemory:
    """
    Implements skillforge.protocols.Memory for the Archie OCI context store.

    Bridges Forge's generic MemorySnapshot contract to the existing
    context_store / archie_memory infrastructure without modifying either.
    """

    def __init__(self, store: ObjectStoreBase) -> None:
        self._store = store

    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot:
        """
        Build a MemorySnapshot from the Archie context store blob.
        """
        archie_state = get_archie_state(context)
        constraints = dict(archie_state.get("latest_approved_constraints") or {})

        facts: dict[str, Any] = {}
        facts_summary = str(archie_state.get("facts_summary") or "").strip()
        if facts_summary:
            facts["facts_summary"] = facts_summary

        infrastructure_profile = archie_state.get("infrastructure_profile")
        if isinstance(infrastructure_profile, dict) and infrastructure_profile:
            facts["infrastructure_profile"] = dict(infrastructure_profile)

        if constraints:
            facts["constraints"] = constraints

        resolved_questions = archie_state.get("resolved_questions")
        if isinstance(resolved_questions, list) and resolved_questions:
            facts["resolved_questions"] = list(resolved_questions)

        return MemorySnapshot(
            session_id=session_id,
            facts=facts,
            constraints=constraints,
            prior_artifacts=_prior_artifacts(context),
            decision_context=dict(context.get("latest_decision_context") or {}),
            raw=context,
        )

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Return updated context; most tool handlers manage their own persistence.
        """
        if tool_name == "generate_diagram" and result.artifact_key:
            agents = context.setdefault("agents", {})
            diagram_ctx = agents.setdefault("diagram", {})
            diagram_ctx["diagram_key"] = result.artifact_key
            diagram_ctx["drawio_key"] = result.artifact_key
            if result.data:
                if result.data.get("diagram_name"):
                    diagram_ctx["diagram_name"] = result.data["diagram_name"]
                if result.summary:
                    diagram_ctx["summary"] = result.summary

            customer_id = str(context.get("customer_id") or "").strip()
            if customer_id and self._store is not None:
                write_context(self._store, customer_id, context)

        return context


def _prior_artifacts(context: dict[str, Any]) -> dict[str, str]:
    agents = context.get("agents", {})
    if not isinstance(agents, dict):
        return {}

    bom = agents.get("bom", {})
    prior = {
        "generate_diagram": _artifact_key(agents.get("diagram", {}), "diagram_key"),
        "generate_bom": _first_artifact_key(bom, ("xlsx_key", "artifact_key")),
        "generate_pov": _artifact_key(agents.get("pov", {}), "artifact_key"),
        "generate_jep": _artifact_key(agents.get("jep", {}), "artifact_key"),
        "generate_waf": _artifact_key(agents.get("waf", {}), "artifact_key"),
        "generate_terraform": _artifact_key(
            agents.get("terraform", {}), "artifact_key"
        ),
    }
    return {name: key for name, key in prior.items() if key}


def _artifact_key(agent_state: Any, field: str) -> str:
    if not isinstance(agent_state, dict):
        return ""
    value = agent_state.get(field, "")
    return value if isinstance(value, str) and value else ""


def _first_artifact_key(agent_state: Any, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _artifact_key(agent_state, field)
        if value:
            return value
    return ""

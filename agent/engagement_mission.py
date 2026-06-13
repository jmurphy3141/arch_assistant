"""
agent/engagement_mission.py
----------------------------
MissionTracker implementation for Archie.

Tracks the C3E engagement phase and completed artifacts in the context dict.
Implements skillforge.protocols.MissionTracker — all methods are pure context
mutations; no direct I/O (archie_session.py owns context persistence).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agent.context_store import get_archie_state

logger = logging.getLogger(__name__)

C3E_PHASE_ORDER = [
    "Qualify", "Develop", "Discover", "Design",
    "Prove", "Win", "Deploy", "Support", "Grow",
]

# Which artifacts must be completed to consider a phase done.
# Phases not listed here (Qualify, Support, Grow) have no artifact gate.
PHASE_ARTIFACTS: dict[str, set[str]] = {
    "Develop":  {"pov"},
    "Discover": {"sta"},
    "Design":   {"diagram", "bom"},
    "Prove":    {"jep", "waf"},
    "Win":      {"technical_proposal"},
    "Deploy":   {"terraform"},
}

# Maps Forge tool name → (phase, artifact_key)
TOOL_TO_ARTIFACT: dict[str, tuple[str, str]] = {
    "generate_pov":                ("Develop",  "pov"),
    "generate_sta":                ("Discover", "sta"),
    "generate_diagram":            ("Design",   "diagram"),
    "generate_bom":                ("Design",   "bom"),
    "generate_jep":                ("Prove",    "jep"),
    "generate_waf":                ("Prove",    "waf"),
    "generate_technical_proposal": ("Win",      "technical_proposal"),
    "generate_terraform":          ("Deploy",   "terraform"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_mission(context: dict[str, Any]) -> dict[str, Any]:
    """Initialise mission state from already-completed artifacts in context."""
    completed: set[str] = set()
    agents = context.get("agents", {})
    # An agent entry with a non-empty key means it's been run at least once.
    tool_artifact_map = {
        "pov": agents.get("pov", {}).get("latest_key", ""),
        "sta": agents.get("sta", {}).get("latest_key", ""),
        "diagram": agents.get("diagram", {}).get("diagram_key", ""),
        "bom": agents.get("bom", {}).get("xlsx_key", ""),
        "jep": agents.get("jep", {}).get("latest_key", ""),
        "waf": agents.get("waf", {}).get("latest_key", ""),
        "technical_proposal": agents.get("technical_proposal", {}).get("latest_key", ""),
        "terraform": agents.get("terraform", {}).get("artifact_key", ""),
    }
    for artifact, key in tool_artifact_map.items():
        if key:
            completed.add(artifact)

    # Derive current phase: highest phase where all required artifacts are done,
    # then advance to the next phase.
    current_phase = "Qualify"
    for phase in C3E_PHASE_ORDER:
        required = PHASE_ARTIFACTS.get(phase, set())
        if required and not required.issubset(completed):
            break
        current_phase = phase

    return {
        "phase": current_phase,
        "completed_artifacts": list(completed),
        "last_updated": _now(),
    }


class EngagementMission:
    """Stateless MissionTracker; reads/writes archie.mission in context dict."""

    def get_mission(self, context: dict[str, Any]) -> dict[str, Any] | None:
        archie = get_archie_state(context)
        mission = archie.get("mission")
        if not mission:
            mission = _init_mission(context)
            archie["mission"] = mission

        phase = mission.get("phase", "Qualify")
        completed = set(mission.get("completed_artifacts", []))
        required = PHASE_ARTIFACTS.get(phase, set())
        next_required = sorted(required - completed) if required else []

        # Surface blockers: missing discovery fields
        client_facts = archie.get("client_facts") or {}
        mem = archie.get("memory", {}).get("client_facts", {})
        economic_buyer = (
            client_facts.get("economic_buyer")
            or mem.get("economic_buyer")
            or context.get("economic_buyer", "")
        )
        current_platform = (
            client_facts.get("platform")
            or mem.get("platform")
            or context.get("current_platform", "")
        )
        blockers = []
        if not economic_buyer:
            blockers.append("economic_buyer unknown")
        if not current_platform:
            blockers.append("current_platform unknown")

        return {
            "phase": phase,
            "completed_artifacts": sorted(completed),
            "next_required": next_required,
            "blockers": blockers,
        }

    def record_milestone(
        self, context: dict[str, Any], tool: str, artifact_key: str
    ) -> None:
        mapping = TOOL_TO_ARTIFACT.get(tool)
        if not mapping:
            return
        phase_for_tool, artifact_name = mapping

        archie = get_archie_state(context)
        mission = archie.setdefault("mission", _init_mission(context))
        completed: set[str] = set(mission.get("completed_artifacts", []))
        completed.add(artifact_name)
        mission["completed_artifacts"] = sorted(completed)
        mission["last_updated"] = _now()

        # Auto-advance phase when all required artifacts for current phase are done
        current_phase = mission.get("phase", "Qualify")
        required = PHASE_ARTIFACTS.get(current_phase, set())
        if required and required.issubset(completed):
            idx = C3E_PHASE_ORDER.index(current_phase) if current_phase in C3E_PHASE_ORDER else -1
            if 0 <= idx < len(C3E_PHASE_ORDER) - 1:
                new_phase = C3E_PHASE_ORDER[idx + 1]
                mission["phase"] = new_phase
                logger.info(
                    "[MISSION] Phase advanced %s → %s (completed: %s)",
                    current_phase, new_phase, sorted(completed),
                )

    def get_phase_warning(
        self, context: dict[str, Any], hat_c3e_phase: str
    ) -> str | None:
        if hat_c3e_phase not in C3E_PHASE_ORDER:
            return None
        archie = get_archie_state(context)
        mission = archie.get("mission") or _init_mission(context)
        current_phase = mission.get("phase", "Qualify")
        if current_phase not in C3E_PHASE_ORDER:
            return None
        gap = C3E_PHASE_ORDER.index(hat_c3e_phase) - C3E_PHASE_ORDER.index(current_phase)
        if gap >= 2:
            return (
                f"Engagement is at C3E {current_phase} phase but this tool belongs to "
                f"{hat_c3e_phase}. Confirm the SE intends to skip ahead before proceeding."
            )
        return None

    def get_briefing(self, context: dict[str, Any], customer_name: str) -> str:
        """Return a compact briefing block for session-open injection."""
        mission = self.get_mission(context)
        if not mission:
            return ""
        # Only emit briefing if engagement has prior work
        if not mission["completed_artifacts"] and not context.get("agents"):
            return ""
        phase = mission["phase"]
        completed = ", ".join(mission["completed_artifacts"]) or "none yet"
        next_req = ", ".join(mission["next_required"]) or "none — phase complete"
        blockers = ", ".join(mission["blockers"]) or "none"
        return (
            f"[Engagement Briefing]\n"
            f"Customer: {customer_name} | C3E Phase: {phase}\n"
            f"Completed artifacts: {completed}\n"
            f"Next required: {next_req}\n"
            f"Blockers: {blockers}\n"
            f"[/Engagement Briefing]"
        )

    def suggest_next_step(
        self,
        context: dict[str, Any],
        tools_called_this_turn: list[str],
    ) -> str | None:
        """Return a one-line next-step offer if no generation tool fired this turn."""
        generation_tools = set(TOOL_TO_ARTIFACT.keys())
        if any(t in generation_tools for t in tools_called_this_turn):
            return None
        mission = self.get_mission(context)
        if not mission:
            return None
        next_required = mission.get("next_required", [])
        if not next_required:
            return None
        phase = mission.get("phase", "")
        artifact = next_required[0]
        tool_name = next(
            (tool for tool, (_, art) in TOOL_TO_ARTIFACT.items() if art == artifact), None
        )
        if not tool_name:
            return None
        tool_label = tool_name.replace("generate_", "").replace("_", " ")
        return (
            f"You're in {phase} phase — {artifact} is the next required artifact. "
            f"Want me to generate the {tool_label}?"
        )

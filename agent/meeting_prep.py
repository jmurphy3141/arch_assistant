"""
agent/meeting_prep.py
---------------------
Deterministic meeting prep assembler.
"""
from __future__ import annotations
from typing import Any
from agent.context_store import get_archie_state

_TOOL_LABELS: dict[str, str] = {
    "generate_diagram": "diagram",
    "generate_bom": "BOM",
    "generate_waf": "WAF review",
    "generate_pov": "POV",
    "generate_jep": "JEP",
    "generate_terraform": "Terraform",
}

_DISCOVERY_PRIORITY = [
    ("platform", "What is the current platform / infrastructure (on-prem, cloud, hypervisor)?"),
    ("workloads", "What are the primary workloads being moved or built on OCI?"),
    ("sizing", "What sizing is needed — CPU, memory, storage per tier?"),
    ("connectivity", "What connectivity is required — VPN, FastConnect, public internet?"),
    ("dr", "What are the DR / RTO / RPO requirements?"),
    ("region", "Which OCI region(s) are required?"),
    ("security", "What security requirements exist — WAF, bastion, compliance frameworks?"),
    ("databases", "Which databases are in scope and do BYOL licenses apply?"),
]


def build_meeting_prep(context: dict[str, Any], customer_name: str = "") -> str:
    archie = get_archie_state(context)
    lines: list[str] = []
    name_label = f" — {customer_name}" if customer_name else ""
    lines.append(f"# Meeting Prep{name_label}")

    mission_data = archie.get("mission") or context.get("agents", {}).get("mission") or {}
    if isinstance(mission_data, dict) and mission_data:
        phase = mission_data.get("phase", "unknown")
        blockers = mission_data.get("blockers", []) or []
        next_required = mission_data.get("next_required", []) or []
        lines.append("\n## Engagement Status")
        lines.append(f"- Current phase: **{phase}**")
        if blockers:
            lines.append("- Blockers to resolve:")
            for b in blockers:
                lines.append(f"  - {b}")
        if next_required:
            lines.append("- Next required artifacts:")
            for n in next_required:
                lines.append(f"  - {n}")

    client_facts = archie.get("client_facts") or {}
    infra_profile = archie.get("infrastructure_profile") or {}
    memory_facts = (archie.get("memory") or {}).get("client_facts") or {}
    gaps = []
    for field, question in _DISCOVERY_PRIORITY:
        val = client_facts.get(field) or infra_profile.get(field) or memory_facts.get(field)
        if not val or (isinstance(val, (list, dict)) and not val):
            gaps.append(question)
    if gaps:
        lines.append("\n## Discovery Questions to Ask")
        for q in gaps:
            lines.append(f"- {q}")

    relationship = archie.get("relationship") or {}
    open_obj = [o for o in (relationship.get("objections") or []) if isinstance(o, dict) and o.get("status") != "addressed"]
    if open_obj:
        lines.append("\n## Open Objections to Address")
        for o in open_obj:
            raised = o.get("raised_by") or "unknown"
            lines.append(f"- **{o.get('concern', '?')}** (raised by: {raised})")
            if o.get("response"):
                lines.append(f"  Response: {o['response']}")

    open_commits = [c for c in (relationship.get("commitments") or []) if isinstance(c, dict) and c.get("status") != "done"]
    if open_commits:
        lines.append("\n## Commitments to Confirm / Deliver")
        for c in open_commits:
            due = f" (due: {c['due']})" if c.get("due") else ""
            lines.append(f"- [{c.get('who', '?')}] {c.get('what', '?')}{due}")

    open_actions = [a for a in (relationship.get("action_items") or []) if isinstance(a, dict) and a.get("status") != "done"]
    if open_actions:
        lines.append("\n## Open Action Items")
        for a in open_actions:
            due = f" (due: {a['due']})" if a.get("due") else ""
            lines.append(f"- [{a.get('owner', '?')}] {a.get('task', '?')}{due}")

    stakeholders = [s for s in (relationship.get("stakeholders") or []) if isinstance(s, dict)]
    if stakeholders:
        lines.append("\n## Stakeholder Map")
        for s in stakeholders:
            notes = f" — {s['notes']}" if s.get("notes") else ""
            lines.append(f"- **{s.get('name', '?')}** | {s.get('role', '?')} | {s.get('disposition', 'unknown')}{notes}")

    lessons: list[dict] = archie.get("lessons") or []
    if lessons:
        lines.append("\n## What Archie Learned on This Engagement")
        by_tool: dict[str, list[str]] = {}
        for lesson in lessons[-10:]:
            tool = lesson.get("tool") or "general"
            label = _TOOL_LABELS.get(tool, tool)
            by_tool.setdefault(label, []).append(
                f"  - ({lesson.get('count', 1)}x) {lesson['concern']}"
            )
        for label, concerns in by_tool.items():
            lines.append(f"- **{label}**:")
            lines.extend(concerns)

    if len(lines) <= 1:
        lines.append("\n_No context yet. Ask the customer about workloads, platform, and goals._")
    return "\n".join(lines)

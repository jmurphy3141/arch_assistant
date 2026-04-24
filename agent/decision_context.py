from __future__ import annotations

import re
from typing import Any


_REGION_RE = re.compile(r"\b[a-z]{2}-[a-z]+-\d\b")
_AVAILABILITY_RE = re.compile(r"\b99(?:\.\d+)?%")
_COST_RE = re.compile(
    r"(?:budget|cost|spend|under|cap|ceiling|max(?:imum)?)"
    r"[^0-9$]{0,12}\$?\s*([0-9]+(?:[.,][0-9]+)?)([kKmM]?)"
)

_SECURITY_KEYWORDS = (
    ("private", "private-only networking"),
    ("waf", "waf protection"),
    ("zero trust", "zero-trust access"),
    ("encrypt", "encryption at rest and in transit"),
    ("security", "security controls"),
    ("iam", "least-privilege iam"),
)

_COMPLIANCE_KEYWORDS = (
    ("pci", "pci"),
    ("hipaa", "hipaa"),
    ("soc 2", "soc2"),
    ("fedramp", "fedramp"),
)

_SUCCESS_PATTERNS = (
    ("horizontally scalable", "Horizontally scalable"),
    ("no single point of failure", "No single point of failure"),
    ("high availability", "High availability"),
    ("resilient", "Resilient under failure"),
    ("low latency", "Low-latency response"),
)


def build_decision_context(
    *,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg = (user_message or "").strip()
    msg_lc = msg.lower()
    context = context or {}

    region = _extract_region(msg_lc) or _extract_region(_context_text(context).lower())
    availability = _extract_availability(msg_lc)
    cost_max_monthly = _extract_cost_limit(msg_lc)
    security_requirements = _extract_keyword_requirements(msg_lc, _SECURITY_KEYWORDS)
    compliance_requirements = _extract_keyword_requirements(msg_lc, _COMPLIANCE_KEYWORDS)
    deployment_preferences = _deployment_preferences(msg_lc)
    success_criteria = _success_criteria(msg_lc)
    assumption_mode = _assumption_mode_requested(msg_lc)
    conversational_architecture = _is_conversational_architecture_prompt(msg_lc)

    assumptions: list[dict[str, str]] = []
    missing_inputs: list[str] = []

    if region is None:
        assumptions.append(
            {
                "id": "region_default",
                "statement": "Region not specified; assume primary OCI region from current tenancy preference.",
                "reason": "No explicit OCI region found in the request or current context.",
                "risk": "medium",
            }
        )
        missing_inputs.append("preferred OCI region")

    if availability is None:
        assumptions.append(
            {
                "id": "availability_default",
                "statement": "Availability target assumed at 99.9%.",
                "reason": "No explicit availability objective was provided.",
                "risk": "low",
            }
        )

    if cost_max_monthly is None and any(token in msg_lc for token in ("cost", "budget", "bom", "pricing", "spend")):
        assumptions.append(
            {
                "id": "cost_unbounded",
                "statement": "No explicit monthly budget cap provided.",
                "reason": "The request references cost but does not define a budget ceiling.",
                "risk": "medium" if assumption_mode else "high",
            }
        )
        missing_inputs.append("monthly budget cap")

    if not security_requirements and any(token in msg_lc for token in ("public", "internet", "external")):
        assumptions.append(
            {
                "id": "internet_security_baseline",
                "statement": "Public ingress requires baseline security controls including WAF and least privilege.",
                "reason": "The request implies internet exposure without listing controls.",
                "risk": "high",
            }
        )

    if not success_criteria and deployment_preferences:
        success_criteria = ["Architecture aligns to declared deployment preferences."]

    requires_user_confirmation = any(a["risk"] == "high" for a in assumptions)
    risk_level = _risk_level_for_assumptions(assumptions)

    return {
        "goal": msg or "Clarify OCI architecture requirements.",
        "assumption_mode": assumption_mode,
        "conversational_architecture": conversational_architecture,
        "risk_level": risk_level,
        "constraints": {
            "region": region,
            "availability_target": availability,
            "cost_max_monthly": cost_max_monthly,
            "security_requirements": security_requirements,
            "compliance_requirements": compliance_requirements,
            "deployment_preferences": deployment_preferences,
        },
        "assumptions": assumptions,
        "success_criteria": success_criteria,
        "missing_inputs": missing_inputs,
        "requires_user_confirmation": requires_user_confirmation,
    }


def derive_constraint_tags(decision_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(decision_context, dict):
        return []
    constraints = decision_context.get("constraints", {}) or {}
    assumptions = decision_context.get("assumptions", []) or []
    tags: set[str] = set()

    if constraints.get("cost_max_monthly") is not None:
        tags.add("cost_sensitive")
    if constraints.get("security_requirements") or constraints.get("compliance_requirements"):
        tags.add("security_sensitive")
    if constraints.get("compliance_requirements"):
        tags.add("compliance_required")
    if constraints.get("region"):
        tags.add("region_pinned")

    availability = str(constraints.get("availability_target", "") or "")
    if availability in {"99.9%", "99.95%", "99.99%", "99.999%"}:
        tags.add("ha_required")

    deployment_preferences = {str(item).strip().lower() for item in constraints.get("deployment_preferences", [])}
    if {"multi_ad", "multi_region"} & deployment_preferences:
        tags.add("ha_required")

    if any(str(a.get("risk", "")).lower() == "high" for a in assumptions):
        tags.add("high_risk_assumptions")

    return sorted(tags)


def summarize_decision_context(decision_context: dict[str, Any] | None) -> str:
    if not isinstance(decision_context, dict):
        return ""
    constraints = decision_context.get("constraints", {}) or {}
    assumptions = decision_context.get("assumptions", []) or []
    success_criteria = decision_context.get("success_criteria", []) or []
    missing_inputs = decision_context.get("missing_inputs", []) or []

    lines = ["Decision context:"]
    lines.append(f"- Goal: {decision_context.get('goal', '')}")
    if decision_context.get("assumption_mode"):
        lines.append("- Mode: best-effort draft from sparse notes")
    if decision_context.get("conversational_architecture"):
        lines.append("- Interaction: architecture discussion / copilot mode")
    if constraints:
        lines.append(
            "- Constraints: "
            + ", ".join(
                [
                    f"region={constraints.get('region') or 'unspecified'}",
                    f"availability={constraints.get('availability_target') or 'unspecified'}",
                    f"cost_max_monthly={constraints.get('cost_max_monthly') if constraints.get('cost_max_monthly') is not None else 'unspecified'}",
                ]
            )
        )
    if success_criteria:
        lines.append("- Success: " + "; ".join(str(item) for item in success_criteria[:3]))
    if assumptions:
        rendered = []
        for assumption in assumptions[:3]:
            rendered.append(
                f"{assumption.get('statement', '')} (risk={assumption.get('risk', 'low')})"
            )
        lines.append("- Assumptions: " + "; ".join(rendered))
    if missing_inputs:
        lines.append("- Missing inputs: " + ", ".join(str(item) for item in missing_inputs[:3]))
    risk_level = str(decision_context.get("risk_level", "") or "").strip()
    if risk_level:
        lines.append(f"- Risk level: {risk_level}")
    return "\n".join(lines)


def _extract_region(text: str) -> str | None:
    match = _REGION_RE.search(text)
    return match.group(0) if match else None


def _extract_availability(text: str) -> str | None:
    match = _AVAILABILITY_RE.search(text)
    return match.group(0) if match else None


def _extract_cost_limit(text: str) -> float | None:
    match = _COST_RE.search(text)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    suffix = match.group(2).lower()
    if suffix == "k":
        value *= 1000
    elif suffix == "m":
        value *= 1000000
    return round(value, 2)


def _extract_keyword_requirements(text: str, keywords: tuple[tuple[str, str], ...]) -> list[str]:
    found: list[str] = []
    for token, label in keywords:
        if token in text and label not in found:
            found.append(label)
    return found


def _deployment_preferences(text: str) -> list[str]:
    found: list[str] = []
    if "multi ad" in text or "multi-ad" in text:
        found.append("multi_ad")
    if "multi region" in text or "multi-region" in text:
        found.append("multi_region")
    if "private" in text:
        found.append("private_networking")
    if "dr" in text or "disaster recovery" in text:
        found.append("dr_ready")
    return found


def _success_criteria(text: str) -> list[str]:
    found: list[str] = []
    for token, label in _SUCCESS_PATTERNS:
        if token in text and label not in found:
            found.append(label)
    return found


def _context_text(context: dict[str, Any]) -> str:
    if not isinstance(context, dict):
        return ""
    archie = context.get("archie", {}) if isinstance(context.get("archie"), dict) else {}
    parts = [
        str(context.get("customer_name", "") or ""),
        str(context.get("latest_decision_context", {}).get("goal", "") or ""),
        str(archie.get("engagement_summary", "") or ""),
        str(archie.get("latest_notes_summary", "") or ""),
        str(archie.get("latest_approved_constraints", {}) or ""),
    ]
    resolved = archie.get("resolved_questions", []) if isinstance(archie.get("resolved_questions"), list) else []
    for item in resolved[-5:]:
        if not isinstance(item, dict):
            continue
        parts.append(str(item.get("question", "") or ""))
        parts.append(str(item.get("final_answer", "") or item.get("suggested_answer", "") or ""))
    pending = context.get("pending_checkpoint") or {}
    if isinstance(pending, dict):
        parts.append(str(pending.get("prompt", "") or ""))
    return " ".join(parts)


def _assumption_mode_requested(text: str) -> bool:
    markers = (
        "assumption",
        "assume",
        "ballpark",
        "ball park",
        "rough",
        "draft",
        "only got",
        "small set of info",
        "notes",
        "sensible defaults",
        "safe assumptions",
    )
    return any(marker in text for marker in markers)


def _is_conversational_architecture_prompt(text: str) -> bool:
    if not text:
        return False
    architecture_markers = (
        "architecture",
        "topology",
        "design",
        "tradeoff",
        "trade-off",
        "option",
        "should we",
        "thinking through",
        "talk through",
        "walk me through",
    )
    deliverable_markers = (
        "generate",
        "create",
        "build",
        "draft a pov",
        "write a pov",
        "terraform",
        "bom",
        "diagram",
        "drawio",
        "draw.io",
        "jep",
        "waf",
    )
    return any(marker in text for marker in architecture_markers) and not any(
        marker in text for marker in deliverable_markers
    )


def _risk_level_for_assumptions(assumptions: list[dict[str, str]]) -> str:
    risks = {str(item.get("risk", "") or "").strip().lower() for item in assumptions if isinstance(item, dict)}
    if "high" in risks:
        return "high"
    if "medium" in risks:
        return "medium"
    return "low"

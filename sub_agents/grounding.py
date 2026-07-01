"""Shared deterministic input/output grounding checks for producer sub-agents."""

from __future__ import annotations

import json
import re
from typing import Any


_CONTROL_FIELDS = {
    "customer_id",
    "customer_name",
    "engagement_id",
    "facts",
    "client_facts",
    "trace_id",
    "feedback",
    "prior_version",
    "prior_version_key",
    "prior_version_number",
    "repair_mode",
    "review_findings",
}


def normalize_engagement_context(value: Any) -> dict[str, Any]:
    """Return a copy with canonical customer identity and current fact fields."""
    context = dict(value) if isinstance(value, dict) else {}
    archie = context.get("archie") if isinstance(context.get("archie"), dict) else {}
    customer_id = str(
        context.get("customer_id")
        or context.get("engagement_id")
        or archie.get("customer_id")
        or ""
    ).strip()
    customer_name = str(
        context.get("customer_name")
        or archie.get("customer_name")
        or ""
    ).strip()
    facts = context.get("facts")
    if not isinstance(facts, dict) or not facts:
        facts = context.get("client_facts")
    if not isinstance(facts, dict) or not facts:
        facts = archie.get("client_facts")
    if not isinstance(facts, dict) or not facts:
        memory = archie.get("memory") if isinstance(archie.get("memory"), dict) else {}
        facts = memory.get("client_facts")
    if not isinstance(facts, dict) or not facts:
        facts = {
            key: item
            for key, item in context.items()
            if key not in _CONTROL_FIELDS and item not in (None, "", [], {})
        }
    context["customer_id"] = customer_id
    context["customer_name"] = customer_name
    context["facts"] = dict(facts) if isinstance(facts, dict) else {}
    return context


def grounding_prompt(context: dict[str, Any]) -> str:
    """Render only the canonical identity and facts for producer prompt grounding."""
    normalized = normalize_engagement_context(context)
    if not any(
        (
            normalized.get("customer_id"),
            normalized.get("customer_name"),
            normalized.get("facts"),
        )
    ):
        return ""
    payload = {
        "customer_id": normalized.get("customer_id", ""),
        "customer_name": normalized.get("customer_name", ""),
        "facts": normalized.get("facts", {}),
    }
    return (
        "[Engagement Grounding]\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        + "\n[End Engagement Grounding]"
    )


def input_grounding_missing(
    context: dict[str, Any], *, require_customer_name: bool = False
) -> list[str]:
    """Validate manager-supplied grounding; empty direct calls have no manager contract."""
    if not context:
        return []
    normalized = normalize_engagement_context(context)
    missing: list[str] = []
    if require_customer_name and not normalized.get("customer_name"):
        missing.append("customer_name")
    elif not normalized.get("customer_name") and not normalized.get("customer_id"):
        missing.append("customer_identity")
    if not normalized.get("facts"):
        missing.append("facts")
    return missing


def output_grounding_missing(
    context: dict[str, Any],
    output: str,
    *,
    require_customer_name: bool = False,
    output_kind: str = "prose",
) -> list[str]:
    """Verify producer input/identity and prose fact reflection when applicable."""
    if not context:
        return []
    normalized = normalize_engagement_context(context)
    missing = input_grounding_missing(
        normalized, require_customer_name=require_customer_name
    )
    if missing:
        return missing
    output_lower = str(output or "").lower()
    customer_name = str(normalized.get("customer_name") or "").strip()
    if require_customer_name and customer_name.lower() not in output_lower:
        missing.append("customer_name_not_reflected")
    if output_kind == "prose":
        anchors = _fact_anchors(normalized.get("facts", {}))
        if anchors and not any(anchor in output_lower for anchor in anchors):
            missing.append("facts_not_reflected")
    return missing


def grounding_trace(
    context: dict[str, Any], missing: list[str]
) -> dict[str, Any]:
    normalized = normalize_engagement_context(context)
    return {
        "grounded": not missing,
        "missing": list(dict.fromkeys(missing)),
        "customer_id": normalized.get("customer_id", ""),
        "customer_name": normalized.get("customer_name", ""),
    }


def needs_input_message(missing: list[str]) -> str:
    fields = ", ".join(dict.fromkeys(missing)) or "engagement grounding"
    return (
        "Grounding review needs input before an artifact can be produced. "
        f"Missing or ungrounded fields: {fields}."
    )


def _fact_anchors(value: Any) -> list[str]:
    anchors: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            anchors.extend(_fact_anchors(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            anchors.extend(_fact_anchors(item))
    elif isinstance(value, str):
        cleaned = re.sub(r"\s+", " ", value).strip().lower()
        if len(cleaned) >= 3 and cleaned not in {"unknown", "not stated", "none"}:
            anchors.append(cleaned)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        anchors.append(str(value).lower())
    return list(dict.fromkeys(anchors))

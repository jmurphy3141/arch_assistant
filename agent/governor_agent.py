from __future__ import annotations

import json
import re
from typing import Any, Callable


_GOVERNOR_SYSTEM = (
    "You are a strict OCI architecture governor. "
    "Enforce security decisions deterministically, require explicit checkpoints for cost overruns, "
    "and provide actionable quality revision guidance only. "
    "Return only compact JSON."
)


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _default_payload() -> dict[str, Any]:
    return {
        "overall_status": "pass",
        "security": {"status": "pass", "findings": [], "required_actions": []},
        "cost": {
            "status": "pass",
            "estimated_monthly_cost": None,
            "budget_target": None,
            "variance": None,
            "findings": [],
        },
        "quality": {
            "status": "pass",
            "issues": [],
            "suggestions": [],
            "confidence": 0,
            "summary": "",
            "severity": "low",
            "overall_pass": True,
        },
        "decision_summary": "",
        "reason_codes": [],
    }


def _parse_payload(raw: str) -> dict[str, Any]:
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError("governor payload is not an object")

    if "overall_status" not in parsed and any(
        key in parsed for key in ("overall_pass", "issues", "suggestions", "critique_summary")
    ):
        overall_pass = bool(parsed.get("overall_pass", True))
        severity = str(parsed.get("severity", "low") or "low")
        confidence = int(max(0, min(100, int(parsed.get("confidence", 0) or 0))))
        issues = [str(item).strip() for item in parsed.get("issues", []) if str(item).strip()]
        suggestions = [str(item).strip() for item in parsed.get("suggestions", []) if str(item).strip()]
        critique_summary = str(parsed.get("critique_summary", "")).strip()
        parsed = {
            "overall_status": "pass" if overall_pass else "revise",
            "security": {"status": "pass", "findings": [], "required_actions": []},
            "cost": {
                "status": "pass",
                "estimated_monthly_cost": None,
                "budget_target": None,
                "variance": None,
                "findings": [],
            },
            "quality": {
                "status": "pass" if overall_pass else "revise",
                "issues": issues,
                "suggestions": suggestions,
                "confidence": confidence,
                "summary": critique_summary,
                "severity": severity,
            },
            "decision_summary": critique_summary,
            "reason_codes": [],
        }

    payload = _default_payload()
    payload.update({k: v for k, v in parsed.items() if k in payload})

    payload["security"] = _normalize_security(parsed.get("security", {}))
    payload["cost"] = _normalize_cost(parsed.get("cost", {}))
    payload["quality"] = _normalize_quality(parsed.get("quality", {}))
    payload["overall_status"] = _normalize_overall_status(parsed.get("overall_status"))
    payload["decision_summary"] = str(parsed.get("decision_summary", "")).strip()
    payload["reason_codes"] = [
        str(item).strip() for item in parsed.get("reason_codes", []) if str(item).strip()
    ]

    payload["overall_pass"] = payload["overall_status"] in {"pass", "checkpoint_required"}
    payload["confidence"] = int(payload["quality"].get("confidence", 0) or 0)
    payload["issues"] = list(payload["quality"].get("issues", []))
    payload["suggestions"] = list(payload["quality"].get("suggestions", []))
    payload["critique_summary"] = str(payload["quality"].get("summary", "") or "")
    payload["severity"] = str(payload["quality"].get("severity", "low") or "low")
    return payload


def evaluate_tool_result(
    *,
    tool_name: str,
    user_message: str,
    tool_args: dict[str, Any],
    result_summary: str,
    result_data: dict[str, Any],
    decision_context: dict[str, Any] | None = None,
    text_runner: Callable[[str, str], str],
) -> dict[str, Any]:
    prompt = (
        "Evaluate whether the specialist output is acceptable for the SA request.\n"
        "Tool: " + tool_name + "\n"
        "User request: " + (user_message or "") + "\n"
        "Tool args: " + json.dumps(tool_args or {}, ensure_ascii=True) + "\n"
        "Decision context: " + json.dumps(decision_context or {}, ensure_ascii=True)[:4000] + "\n"
        "Tool summary: " + (result_summary or "")[:3000] + "\n"
        "Tool data: " + json.dumps(result_data or {}, ensure_ascii=True)[:6000] + "\n\n"
        "Return ONLY JSON with exact keys:\n"
        '{"overall_status":"pass|revise|blocked|checkpoint_required",'
        '"security":{"status":"pass|blocked","findings":["..."],"required_actions":["..."]},'
        '"cost":{"status":"pass|checkpoint_required","estimated_monthly_cost":null,"budget_target":null,"variance":null,"findings":["..."]},'
        '"quality":{"status":"pass|revise","issues":["..."],"suggestions":["..."],"confidence":0,"summary":"","severity":"low|medium|high"},'
        '"decision_summary":"...",'
        '"reason_codes":["..."]}'
    )
    raw = text_runner(prompt, _GOVERNOR_SYSTEM)
    payload = _parse_payload(raw)
    _apply_deterministic_overrides(
        payload=payload,
        tool_name=tool_name,
        decision_context=decision_context or {},
        result_data=result_data or {},
        result_summary=result_summary or "",
    )
    payload["overall_pass"] = payload["overall_status"] in {"pass", "checkpoint_required"}
    payload["confidence"] = int(payload["quality"].get("confidence", 0) or 0)
    payload["issues"] = list(payload["quality"].get("issues", []))
    payload["suggestions"] = list(payload["quality"].get("suggestions", []))
    payload["critique_summary"] = str(payload["quality"].get("summary", "") or "")
    payload["severity"] = str(payload["quality"].get("severity", "low") or "low")
    return payload


def _normalize_overall_status(value: Any) -> str:
    normalized = str(value or "pass").strip().lower()
    if normalized not in {"pass", "revise", "blocked", "checkpoint_required"}:
        return "pass"
    return normalized


def _normalize_security(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    status = str(data.get("status", "pass") or "pass").strip().lower()
    if status not in {"pass", "blocked"}:
        status = "pass"
    return {
        "status": status,
        "findings": [str(item).strip() for item in data.get("findings", []) if str(item).strip()],
        "required_actions": [
            str(item).strip() for item in data.get("required_actions", []) if str(item).strip()
        ],
    }


def _normalize_cost(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    status = str(data.get("status", "pass") or "pass").strip().lower()
    if status not in {"pass", "checkpoint_required"}:
        status = "pass"
    return {
        "status": status,
        "estimated_monthly_cost": _to_float_or_none(data.get("estimated_monthly_cost")),
        "budget_target": _to_float_or_none(data.get("budget_target")),
        "variance": _to_float_or_none(data.get("variance")),
        "findings": [str(item).strip() for item in data.get("findings", []) if str(item).strip()],
    }


def _normalize_quality(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    status = str(data.get("status", "pass") or "pass").strip().lower()
    if status not in {"pass", "revise"}:
        status = "pass"
    severity = str(data.get("severity", "low") or "low").strip().lower()
    if severity not in {"low", "medium", "high"}:
        severity = "low"
    confidence = int(max(0, min(100, int(data.get("confidence", 0) or 0))))
    return {
        "status": status,
        "issues": [str(item).strip() for item in data.get("issues", []) if str(item).strip()],
        "suggestions": [str(item).strip() for item in data.get("suggestions", []) if str(item).strip()],
        "confidence": confidence,
        "summary": str(data.get("summary", "") or data.get("critique_summary", "")).strip(),
        "severity": severity,
        "overall_pass": status == "pass",
    }


def _apply_deterministic_overrides(
    *,
    payload: dict[str, Any],
    tool_name: str,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
    result_summary: str,
) -> None:
    constraints = decision_context.get("constraints", {}) if isinstance(decision_context, dict) else {}
    budget = _to_float_or_none(constraints.get("cost_max_monthly"))
    estimated_cost = _extract_estimated_monthly_cost(result_data)
    if budget is not None:
        payload["cost"]["budget_target"] = budget
    if estimated_cost is not None:
        payload["cost"]["estimated_monthly_cost"] = estimated_cost

    if budget is not None and estimated_cost is not None and estimated_cost > budget * 1.10:
        variance = round(estimated_cost - budget, 2)
        payload["cost"]["status"] = "checkpoint_required"
        payload["cost"]["variance"] = variance
        finding = (
            f"Estimated monthly cost {estimated_cost:.2f} exceeds budget target {budget:.2f} by {variance:.2f}."
        )
        if finding not in payload["cost"]["findings"]:
            payload["cost"]["findings"].append(finding)
        if "budget_exceeded" not in payload["reason_codes"]:
            payload["reason_codes"].append("budget_exceeded")
        if payload["overall_status"] != "blocked":
            payload["overall_status"] = "checkpoint_required"
        if not payload["decision_summary"]:
            payload["decision_summary"] = (
                "Cost checkpoint required before accepting this architecture recommendation."
            )

    _apply_single_resource_budget_warning(payload=payload, budget=budget, estimated_cost=estimated_cost, result_data=result_data)
    if _public_ingress_without_waf_or_justification(
        tool_name=tool_name,
        decision_context=decision_context,
        result_data=result_data,
        result_summary=result_summary,
    ):
        _require_checkpoint(
            payload,
            reason_code="public_ingress_without_waf",
            finding="Public ingress for an application workload requires WAF coverage or explicit risk justification.",
            summary="Security checkpoint required before accepting public ingress without WAF or explicit justification.",
            section="security",
        )
    if _uses_root_compartment(decision_context=decision_context, result_data=result_data, result_summary=result_summary):
        _require_checkpoint(
            payload,
            reason_code="root_compartment_usage",
            finding="Root compartment usage requires an explicit architecture checkpoint.",
            summary="Security checkpoint required before accepting root compartment placement.",
            section="security",
        )
    if _has_missing_encryption_signal(result_data=result_data, result_summary=result_summary):
        _require_checkpoint(
            payload,
            reason_code="missing_encryption",
            finding="Block volume or database context is missing encryption.",
            summary="Security checkpoint required before accepting unencrypted block volume or database resources.",
            section="security",
        )
    if _has_high_risk_assumption_with_missing_input(decision_context):
        _require_checkpoint(
            payload,
            reason_code="high_risk_assumption_missing_input",
            finding="A high-risk assumption depends on missing required input.",
            summary="Discovery checkpoint required before accepting high-risk assumptions with missing required inputs.",
            section="quality",
        )
    contradiction = _detect_requirement_contradiction(
        decision_context=decision_context,
        result_data=result_data,
        result_summary=result_summary,
    )
    if contradiction == "blocked":
        _block_output(
            payload,
            reason_code="requirement_contradiction",
            finding="Generated output directly contradicts a structured requirement.",
            summary="Governor blocked this output because it contradicts a stated requirement.",
        )
    elif contradiction == "checkpoint_required":
        _require_checkpoint(
            payload,
            reason_code="requirement_contradiction_unstructured",
            finding="Generated output may contradict a stated requirement and requires confirmation.",
            summary="Checkpoint required before accepting output with a possible requirement contradiction.",
            section="quality",
        )

    summary_lc = result_summary.lower()
    if tool_name == "generate_waf" and any(token in summary_lc for token in ("critical", "high risk", "failed")):
        payload["security"]["status"] = "blocked"
        if "Security review reported blocking findings." not in payload["security"]["findings"]:
            payload["security"]["findings"].append("Security review reported blocking findings.")
        if "security_blocked" not in payload["reason_codes"]:
            payload["reason_codes"].append("security_blocked")

    if payload["security"]["status"] == "blocked":
        payload["overall_status"] = "blocked"
        if not payload["decision_summary"]:
            payload["decision_summary"] = "Security policy blocked this output."

    quality_status = payload["quality"]["status"]
    if payload["overall_status"] == "pass" and quality_status == "revise":
        payload["overall_status"] = "revise"


def _require_checkpoint(
    payload: dict[str, Any],
    *,
    reason_code: str,
    finding: str,
    summary: str,
    section: str,
) -> None:
    if section == "security":
        if finding not in payload["security"]["findings"]:
            payload["security"]["findings"].append(finding)
        if finding not in payload["security"]["required_actions"]:
            payload["security"]["required_actions"].append(finding)
    elif section == "cost":
        if finding not in payload["cost"]["findings"]:
            payload["cost"]["findings"].append(finding)
        payload["cost"]["status"] = "checkpoint_required"
    else:
        if finding not in payload["quality"]["issues"]:
            payload["quality"]["issues"].append(finding)
    if reason_code not in payload["reason_codes"]:
        payload["reason_codes"].append(reason_code)
    if payload["overall_status"] != "blocked":
        payload["overall_status"] = "checkpoint_required"
    if not payload["decision_summary"]:
        payload["decision_summary"] = summary


def _block_output(
    payload: dict[str, Any],
    *,
    reason_code: str,
    finding: str,
    summary: str,
) -> None:
    payload["security"]["status"] = "blocked"
    if finding not in payload["security"]["findings"]:
        payload["security"]["findings"].append(finding)
    if finding not in payload["security"]["required_actions"]:
        payload["security"]["required_actions"].append(finding)
    if reason_code not in payload["reason_codes"]:
        payload["reason_codes"].append(reason_code)
    payload["overall_status"] = "blocked"
    if not payload["decision_summary"]:
        payload["decision_summary"] = summary


def _apply_single_resource_budget_warning(
    *,
    payload: dict[str, Any],
    budget: float | None,
    estimated_cost: float | None,
    result_data: dict[str, Any],
) -> None:
    threshold_base = budget if budget is not None else estimated_cost
    if threshold_base is None or threshold_base <= 0:
        return
    threshold = threshold_base * 0.40
    for item in _extract_cost_line_items(result_data):
        monthly_cost = _to_float_or_none(
            item.get("estimated_monthly_cost")
            or item.get("monthly_cost")
            or item.get("cost_monthly")
            or item.get("monthly")
            or item.get("total_monthly_cost")
        )
        if monthly_cost is None:
            quantity = _to_float_or_none(item.get("quantity") or item.get("qty"))
            unit_cost = _to_float_or_none(item.get("unit_monthly_cost") or item.get("unit_cost"))
            if quantity is not None and unit_cost is not None:
                monthly_cost = round(quantity * unit_cost, 2)
        if monthly_cost is None or monthly_cost <= threshold:
            continue
        label = str(item.get("name") or item.get("description") or item.get("service") or "resource").strip()
        finding = (
            f"Cost concentration warning: {label} is {monthly_cost:.2f}, "
            f"more than 40% of the comparison budget {threshold_base:.2f}."
        )
        if finding not in payload["cost"]["findings"]:
            payload["cost"]["findings"].append(finding)
        if "single_resource_budget_concentration" not in payload["reason_codes"]:
            payload["reason_codes"].append("single_resource_budget_concentration")
        return


def _public_ingress_without_waf_or_justification(
    *,
    tool_name: str,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
    result_summary: str,
) -> bool:
    if tool_name == "generate_waf":
        return False
    text = _searchable_text(decision_context, result_data, result_summary)
    has_public_ingress = any(
        token in text
        for token in (
            "public ingress",
            "public load balancer",
            "internet-facing",
            "internet facing",
            '"public_ingress": true',
            '"ingress": "public"',
            '"exposure": "public"',
        )
    )
    has_app_workload = any(
        token in text
        for token in ("compute", "application", "app server", "oke", "kubernetes", "load balancer", "web")
    )
    has_negative_waf = any(token in text for token in ("without waf", "no waf", "waf disabled", "no web application firewall"))
    has_waf = any(token in text for token in ("waf", "web application firewall")) and not has_negative_waf
    has_justification = any(
        token in text
        for token in ("explicit justification", "accepted risk", "risk acceptance", "justification")
    )
    return has_public_ingress and has_app_workload and not has_waf and not has_justification


def _uses_root_compartment(
    *,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
    result_summary: str,
) -> bool:
    for key, value in _walk_key_values({"decision_context": decision_context, "result_data": result_data}):
        key_lc = key.lower()
        value_lc = str(value).strip().lower()
        if "compartment" in key_lc and value_lc in {"root", "root compartment", "tenancy root"}:
            return True
    text = _searchable_text(decision_context, result_data, result_summary)
    return "root compartment" in text or "tenancy root" in text


def _has_missing_encryption_signal(*, result_data: dict[str, Any], result_summary: str) -> bool:
    text = _searchable_text(result_data, result_summary)
    if "unencrypted" in text and any(token in text for token in ("block volume", "boot volume", "database", "db system")):
        return True
    for key, value in _walk_key_values(result_data):
        key_lc = key.lower()
        if key_lc in {"encryption", "encrypted", "is_encrypted"} and value is False:
            return True
        if "encryption" in key_lc and str(value).strip().lower() in {"false", "none", "disabled", "missing"}:
            return True
    return False


def _has_high_risk_assumption_with_missing_input(decision_context: dict[str, Any]) -> bool:
    if not isinstance(decision_context, dict):
        return False
    if not list(decision_context.get("missing_inputs", []) or []):
        return False
    assumptions = decision_context.get("assumptions", []) or []
    return any(
        isinstance(item, dict) and str(item.get("risk", "") or "").strip().lower() == "high"
        for item in assumptions
    )


def _detect_requirement_contradiction(
    *,
    decision_context: dict[str, Any],
    result_data: dict[str, Any],
    result_summary: str,
) -> str:
    structured_keys = {
        "contradictions",
        "contradicted_requirements",
        "requirement_conflicts",
        "policy_conflicts",
    }
    for key, value in _walk_key_values(result_data):
        if key.lower() in structured_keys and value:
            return "blocked"

    constraints = decision_context.get("constraints", {}) if isinstance(decision_context, dict) else {}
    security_requirements = " ".join(
        str(item).lower() for item in constraints.get("security_requirements", []) or []
    )
    if "private-only" in security_requirements or "private only" in security_requirements:
        text = _searchable_text(result_data, result_summary)
        if any(
            token in text
            for token in (
                '"public_ingress": true',
                '"ingress": "public"',
                '"exposure": "public"',
                "public ingress",
                "public load balancer",
            )
        ):
            return "blocked"

    summary_lc = result_summary.lower()
    if "contradict" in summary_lc and "requirement" in summary_lc:
        return "checkpoint_required"
    return ""


def _extract_cost_line_items(result_data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    bom_payload = result_data.get("bom_payload")
    if isinstance(bom_payload, dict):
        candidates.extend(list(bom_payload.get("line_items", []) or []))
    candidates.extend(list(result_data.get("line_items", []) or []))
    candidates.extend(list(result_data.get("resources", []) or []))
    return [item for item in candidates if isinstance(item, dict)]


def _walk_key_values(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            yield key_text, child
            yield from _walk_key_values(child, key_text)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_key_values(child, prefix)


def _searchable_text(*parts: Any) -> str:
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, (dict, list)):
            rendered.append(json.dumps(part, ensure_ascii=True, sort_keys=True))
        else:
            rendered.append(str(part or ""))
    return "\n".join(rendered).lower()


def _extract_estimated_monthly_cost(result_data: dict[str, Any]) -> float | None:
    bom_payload = result_data.get("bom_payload")
    if isinstance(bom_payload, dict):
        totals = bom_payload.get("totals", {})
        if isinstance(totals, dict):
            estimated = _to_float_or_none(totals.get("estimated_monthly_cost"))
            if estimated is not None:
                return estimated
    totals = result_data.get("totals", {})
    if isinstance(totals, dict):
        return _to_float_or_none(totals.get("estimated_monthly_cost"))
    return None


def _to_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except Exception:
        return None

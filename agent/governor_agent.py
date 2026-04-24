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

    if budget is not None and estimated_cost is not None and estimated_cost > budget:
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

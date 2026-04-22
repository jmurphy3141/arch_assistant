from __future__ import annotations

import json
import re
from typing import Any, Callable


_CRITIC_SYSTEM = (
    "You are a strict quality critic for OCI specialist agent outputs. "
    "Operating contract: produce concrete pass/fail rationale and actionable fixes only. "
    "Return only compact JSON."
)


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _parse_structured_critic(raw: str) -> dict[str, Any]:
    parsed = json.loads(_strip_fences(raw))
    if not isinstance(parsed, dict):
        raise ValueError("critic payload is not an object")

    issues = parsed.get("issues", [])
    suggestions = parsed.get("suggestions", [])
    severity = str(parsed.get("severity", "low")).strip().lower()
    confidence = int(parsed.get("confidence", 0))

    if not isinstance(issues, list):
        raise ValueError("critic.issues must be a list")
    if not isinstance(suggestions, list):
        raise ValueError("critic.suggestions must be a list")
    if severity not in {"high", "medium", "low"}:
        severity = "low"

    confidence = max(0, min(100, confidence))
    critique_summary = str(parsed.get("critique_summary", "")).strip()
    overall_pass = bool(parsed.get("overall_pass", True))

    return {
        "issues": [str(i).strip() for i in issues if str(i).strip()],
        "severity": severity,
        "suggestions": [str(i).strip() for i in suggestions if str(i).strip()],
        "confidence": confidence,
        "overall_pass": overall_pass,
        "critique_summary": critique_summary,
    }


def evaluate_tool_result(
    *,
    tool_name: str,
    user_message: str,
    tool_args: dict[str, Any],
    result_summary: str,
    result_data: dict[str, Any],
    text_runner: Callable[[str, str], str],
) -> dict[str, Any]:
    """
    Returns strict critic JSON payload:
      {
        "issues": list[str],
        "severity": "high|medium|low",
        "suggestions": list[str],
        "confidence": int,
        "overall_pass": bool,
        "critique_summary": str,
      }

    Raises ValueError on invalid critic JSON.
    """
    prompt = (
        "Evaluate whether the specialist output is good enough for the SA request.\n"
        "Tool: " + tool_name + "\n"
        "User request: " + (user_message or "") + "\n"
        "Tool args: " + json.dumps(tool_args or {}, ensure_ascii=True) + "\n"
        "Tool summary: " + (result_summary or "")[:3000] + "\n"
        "Tool data: " + json.dumps(result_data or {}, ensure_ascii=True)[:6000] + "\n\n"
        "Evaluate against any provided skill guidance, quality bars, and checklist expectations.\n"
        "Return ONLY JSON with exact keys:\n"
        '{"issues": ["..."], "severity": "high|medium|low", "suggestions": ["..."], '
        '"confidence": 0, "overall_pass": true|false, "critique_summary": "..."}'
    )
    raw = text_runner(prompt, _CRITIC_SYSTEM)
    return _parse_structured_critic(raw)

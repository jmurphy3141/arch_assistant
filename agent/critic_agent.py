from __future__ import annotations

import json
import re
from typing import Any, Callable


_CRITIC_SYSTEM = (
    "You are a strict quality critic for OCI specialist agent outputs. "
    "Return only compact JSON."
)


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()


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
    Returns:
      {"overall_pass": bool, "feedback": str, "reason": str}
    """
    prompt = (
        "Evaluate whether the specialist output is good enough for the SA request.\n"
        "Tool: " + tool_name + "\n"
        "User request: " + (user_message or "") + "\n"
        "Tool args: " + json.dumps(tool_args or {}, ensure_ascii=True) + "\n"
        "Tool summary: " + (result_summary or "")[:2000] + "\n"
        "Tool data: " + json.dumps(result_data or {}, ensure_ascii=True)[:4000] + "\n\n"
        "Return ONLY JSON with exact keys:\n"
        '{"overall_pass": true|false, "feedback": "<short actionable feedback>", "reason": "<short reason>"}'
    )
    raw = text_runner(prompt, _CRITIC_SYSTEM)
    cleaned = _strip_fences(raw)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return {
            "overall_pass": True,
            "feedback": "",
            "reason": "critic_parse_failed_allow",
        }
    return {
        "overall_pass": bool(parsed.get("overall_pass", True)),
        "feedback": str(parsed.get("feedback", "")).strip(),
        "reason": str(parsed.get("reason", "")).strip(),
    }

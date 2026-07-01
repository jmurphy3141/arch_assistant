"""
sub_agent_client.py
--------------------
A2A HTTP client for Archie's sub-agents.

Archie calls sub-agents through this module only.
No other orchestrator file may import sub-agent modules directly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from sub_agents.grounding import normalize_engagement_context


_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
_CARD_CACHE: dict[str, dict[str, Any]] = {}


class SubAgentError(Exception):
    pass


def _response_error_detail(body: Any, fallback: str) -> str:
    if not isinstance(body, dict):
        return fallback
    for key in ("error_message", "detail", "result", "message"):
        value = body.get(key)
        if value not in (None, "", [], {}):
            return str(value)
    return fallback


def _load_config() -> dict[str, Any]:
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _sub_agent_url(name: str) -> str:
    env_name = f"ARCHIE_SUB_AGENT_{str(name or '').upper()}_URL"
    env_url = str(os.environ.get(env_name) or "").rstrip("/")
    if env_url:
        return env_url
    config = _load_config()
    sub_agents = config.get("sub_agents") or {}
    if not isinstance(sub_agents, dict):
        raise SubAgentError("config.yaml sub_agents block is invalid")
    base_url = str(sub_agents.get(name) or "").rstrip("/")
    if not base_url:
        raise SubAgentError(f"Sub-agent {name!r} is not configured")
    return base_url


def _declared_inputs(card: dict[str, Any]) -> tuple[set[str], set[str]]:
    inputs = card.get("inputs") if isinstance(card, dict) else {}
    if not isinstance(inputs, dict):
        return set(), set()
    required = inputs.get("required") if isinstance(inputs.get("required"), list) else []
    optional = inputs.get("optional") if isinstance(inputs.get("optional"), list) else []
    return {str(item) for item in required}, {str(item) for item in optional}


async def get_agent_card(name: str) -> dict:
    """
    Returns the agent card for the named sub-agent.
    Cards are cached after the first fetch.
    """
    if name in _CARD_CACHE:
        return _CARD_CACHE[name]

    base_url = _sub_agent_url(name)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{base_url}/a2a/card")
    except Exception as exc:
        raise SubAgentError(f"Failed to fetch agent card for {name!r}: {exc}") from exc

    if response.status_code != 200:
        raise SubAgentError(
            f"Failed to fetch agent card for {name!r}: HTTP {response.status_code}"
        )

    try:
        card = response.json()
    except Exception as exc:
        raise SubAgentError(f"Invalid agent card JSON for {name!r}: {exc}") from exc

    if not isinstance(card, dict):
        raise SubAgentError(f"Invalid agent card for {name!r}: expected object")
    _CARD_CACHE[name] = card
    return card


async def call_sub_agent(
    name: str,
    task: str,
    engagement_context: dict | None = None,
    trace_id: str = "",
) -> dict:
    """
    Calls the named sub-agent via A2A.
    Returns the A2AResponse as a dict: {"result": ..., "status": ..., "trace": ...}
    Raises SubAgentError on HTTP error or non-ok status.
    """
    card = await get_agent_card(name)
    required, optional = _declared_inputs(card)
    if "task" not in required:
        raise SubAgentError(f"Sub-agent {name!r} card does not require task input")

    payload: dict[str, Any] = {"task": str(task or "")}
    if "engagement_context" in required or "engagement_context" in optional:
        payload["engagement_context"] = normalize_engagement_context(
            engagement_context
        )
    if "trace_id" in required or "trace_id" in optional:
        payload["trace_id"] = str(trace_id or "")

    base_url = _sub_agent_url(name)
    last_error: Exception | None = None
    response: httpx.Response | None = None
    # A sub-agent owns its own bounded repair policy. Repeating the identical
    # HTTP request here doubles inference latency and can create duplicate
    # artifacts, so transport dispatch is single-attempt and fail-closed.
    for attempt in range(1):
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(f"{base_url}/a2a", json=payload)
            if response.status_code < 500:
                break
        except Exception as exc:
            last_error = exc
            raise SubAgentError(f"Failed to call sub-agent {name!r}: {exc}") from exc
    if response is None:
        detail = str(last_error) if last_error else "no response"
        raise SubAgentError(f"Failed to call sub-agent {name!r}: {detail}")

    if response.status_code != 200:
        detail = ""
        try:
            detail = _response_error_detail(response.json(), "")
        except Exception:
            detail = response.text
        suffix = f": {detail}" if detail else ""
        raise SubAgentError(
            f"Sub-agent {name!r} returned HTTP {response.status_code}{suffix}"
        )

    try:
        body = response.json()
    except Exception as exc:
        raise SubAgentError(f"Invalid response JSON from sub-agent {name!r}: {exc}") from exc

    if not isinstance(body, dict):
        raise SubAgentError(f"Invalid response from sub-agent {name!r}: expected object")
    status = str(body.get("status") or "").lower()
    if status == "error" or status not in {"ok", "needs_input"}:
        detail = _response_error_detail(body, "error status")
        raise SubAgentError(f"Sub-agent {name!r} returned error status: {detail}")
    return body

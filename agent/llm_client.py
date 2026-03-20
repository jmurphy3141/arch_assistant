"""
agent/llm_client.py
--------------------
Legacy OCI GenAI ADK wrapper for standalone use.

NOTE: The FastAPI server (drawing_agent_server.py) calls the ADK directly.
This module is kept for CLI / notebook usage outside the server context.

Auth: OCI Instance Principal — no ~/.oci/config required.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _load_config() -> dict:
    import yaml
    from pathlib import Path
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    with cfg_path.open() as f:
        return yaml.safe_load(f)


def get_agent():
    """Return a configured OCI GenAI ADK Agent using Instance Principal auth."""
    from oci.addons.adk import Agent, AgentClient

    cfg = _load_config()
    client = AgentClient()

    agent = Agent(
        client=client,
        agent_endpoint_id=cfg["agent_endpoint_id"],
        instructions=(
            "You are an OCI solutions architect and layout compiler. "
            "When given a Bill of Materials, output ONLY valid JSON — "
            "either a layout specification or a clarification request. "
            "No markdown, no explanation, no preamble."
        ),
        tools=[],
    )
    agent.setup()
    return agent


def run_layout_prompt(prompt: str, session_id: Optional[str] = None) -> dict:
    """
    Send a layout compiler prompt to OCI GenAI and return parsed JSON.

    Returns either a layout spec dict or a clarification dict:
      {"status": "need_clarification", "questions": [...]}
    """
    cfg = _load_config()
    agent = get_agent()
    response = agent.run(prompt, session_id=session_id, max_steps=cfg.get("max_steps", 5))

    raw = _extract_text(response)
    logger.debug("LLM raw (%d chars): %s", len(raw), raw[:400])
    return json.loads(_clean_json(raw))


def _extract_text(response) -> str:
    if not hasattr(response, "data"):
        return str(response)
    data = response.data
    if "message" in data:
        msg = data["message"]
        if isinstance(msg, dict):
            text = msg.get("content", {}).get("text")
            if text is not None:
                return text
        if isinstance(msg, str):
            return msg
    for msg in data.get("messages", []):
        if msg.get("role") == "AGENT":
            return msg.get("content", {}).get("text") or ""
    return ""


def _clean_json(raw: str) -> str:
    return raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()

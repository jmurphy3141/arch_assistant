"""
Runtime config helpers for v1.5 additive model settings.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def resolve_agent_llm_config(cfg: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """
    Merge global llm defaults with per-agent overrides.
    Falls back to existing inference settings for backward compatibility.
    """
    defaults = deepcopy(cfg.get("llm_defaults", {}))
    if not defaults:
        inference = cfg.get("inference", {})
        defaults = {
            "service_endpoint": inference.get("service_endpoint", ""),
            "model_id": inference.get("model_id", ""),
            "max_tokens": int(inference.get("max_tokens", 4000)),
            "temperature": float(inference.get("temperature", 0.0)),
            "top_p": float(inference.get("top_p", 0.9)),
            "top_k": int(inference.get("top_k", 0)),
            "timeout_seconds": 120,
            "retries": 2,
        }

    agent_cfg = deepcopy(cfg.get("agents", {}).get(agent_name, {}))
    merged = {**defaults, **agent_cfg}
    if not merged.get("model_id"):
        merged["model_id"] = defaults.get("model_id", "")
    if not merged.get("service_endpoint"):
        merged["service_endpoint"] = defaults.get("service_endpoint", "")
    return merged

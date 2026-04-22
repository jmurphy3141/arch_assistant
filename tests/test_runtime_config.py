from __future__ import annotations

import pytest

from agent.runtime_config import resolve_agent_llm_config


pytestmark = [pytest.mark.unit]


def test_resolve_agent_llm_config_uses_llm_defaults_then_agent_override() -> None:
    cfg = {
        "llm_defaults": {
            "service_endpoint": "https://default.endpoint",
            "model_id": "model-default",
            "max_tokens": 4000,
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 0,
            "timeout_seconds": 120,
            "retries": 2,
        },
        "agents": {
            "terraform": {
                "model_id": "model-grok-code",
                "temperature": 0.1,
                "timeout_seconds": 240,
            }
        },
    }

    resolved = resolve_agent_llm_config(cfg, "terraform")

    assert resolved["service_endpoint"] == "https://default.endpoint"
    assert resolved["model_id"] == "model-grok-code"
    assert resolved["temperature"] == 0.1
    assert resolved["timeout_seconds"] == 240
    assert resolved["max_tokens"] == 4000


def test_resolve_agent_llm_config_falls_back_to_inference_block() -> None:
    cfg = {
        "inference": {
            "service_endpoint": "https://inference.endpoint",
            "model_id": "model-inference",
            "max_tokens": 3000,
            "temperature": 0.0,
            "top_p": 0.95,
            "top_k": 10,
        },
        "agents": {
            "diagram": {
                "model_id": "",
            }
        },
    }

    resolved = resolve_agent_llm_config(cfg, "diagram")

    assert resolved["service_endpoint"] == "https://inference.endpoint"
    assert resolved["model_id"] == "model-inference"
    assert resolved["max_tokens"] == 3000
    assert resolved["temperature"] == 0.0
    assert resolved["top_p"] == 0.95
    assert resolved["top_k"] == 10

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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


def test_committed_native_orchestrator_uses_grok_43_and_native_default() -> None:
    """Guard the deliberate native-mode default and its dedicated model."""
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    native = resolve_agent_llm_config(cfg, "native_orchestrator")
    forge = resolve_agent_llm_config(cfg, "orchestrator")

    assert native["model_id"] == (
        "ocid1.generativeaimodel.oc1.us-chicago-1."
        "amaaaaaask7dceya4fxp5zjj27q24rjxk46l43die7u6nclgwfbemklsdvoa"
    )
    assert forge["model_id"] == cfg["llm_defaults"]["model_id"]
    assert cfg["orchestrator"]["agent_mode"] == "native"

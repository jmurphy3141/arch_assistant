from pathlib import Path
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def test_root_sub_agent_urls_match_agent_config_ports():
    root_config = _load_yaml(ROOT / "config.yaml")
    sub_agents = root_config.get("sub_agents") or {}

    for name, base_url in sub_agents.items():
        config_path = ROOT / "sub_agents" / str(name) / "config.yaml"
        if not config_path.exists():
            continue

        agent_config = _load_yaml(config_path)
        expected_port = int(agent_config["port"])
        parsed = urlparse(str(base_url))

        assert parsed.port == expected_port, (
            f"config.yaml sub_agents.{name} points to port {parsed.port}, "
            f"but {config_path.relative_to(ROOT)} declares {expected_port}"
        )

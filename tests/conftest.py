from __future__ import annotations

from pathlib import Path

import pytest


_MARKERS = {"unit", "integration", "system", "e2e", "prompt_static", "prompt_judge", "live"}


def _has_taxonomy_marker(item: pytest.Item) -> bool:
    return any(mark.name in _MARKERS for mark in item.iter_markers())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    _ = config
    for item in items:
        if _has_taxonomy_marker(item):
            continue

        path = Path(str(item.fspath)).as_posix()

        if "/scenarios/" in path:
            item.add_marker(pytest.mark.e2e)
            continue

        if path.endswith("/test_a2a.py") or path.endswith("/test_chat_history_streaming.py") or path.endswith("/test_orchestrator_system_flow.py"):
            item.add_marker(pytest.mark.system)
            continue

        if path.endswith("/test_specialist_mode_routing.py") or path.endswith("/test_terraform_api.py") or path.endswith("/test_terraform_graph.py"):
            item.add_marker(pytest.mark.integration)
            continue

        item.add_marker(pytest.mark.unit)

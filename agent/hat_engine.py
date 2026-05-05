"""Hat discovery, tool definitions, activation helpers, and prompt injection."""

from __future__ import annotations

from pathlib import Path


_HATS_DIR = Path(__file__).parent / "hats"
MAX_ACTIVE_HATS = 3


def _discover_hats() -> dict[str, str]:
    if not _HATS_DIR.exists():
        return {}
    hats: dict[str, str] = {}
    for path in sorted(_HATS_DIR.glob("*.md")):
        if path.is_file():
            hats[path.stem] = path.read_text(encoding="utf-8")
    return hats


_HAT_CACHE: dict[str, str] = _discover_hats()


def load_hats() -> dict[str, str]:
    """Return cached hat markdown keyed by hat name."""
    return dict(_HAT_CACHE)


def get_hat_tool_definitions() -> list[dict]:
    """Return use/drop tool-call schemas for every discovered hat."""
    tools: list[dict] = []
    parameters = {"type": "object", "properties": {}}
    for name in sorted(_HAT_CACHE):
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"use_hat_{name}",
                    "description": f"Activate the {name} hat for expert reasoning.",
                    "parameters": parameters,
                },
            }
        )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": f"drop_hat_{name}",
                    "description": f"Deactivate the {name} hat.",
                    "parameters": parameters,
                },
            }
        )
    return tools


def apply_hat(active_hats: list[str], hat_name: str) -> list[str]:
    """Return active hats after applying a known hat with FIFO eviction."""
    if hat_name not in _HAT_CACHE:
        raise ValueError(f"Unknown hat: {hat_name}")
    if hat_name in active_hats:
        return active_hats
    if len(active_hats) < MAX_ACTIVE_HATS:
        return active_hats + [hat_name]
    return active_hats[1:] + [hat_name]


def drop_hat(active_hats: list[str], hat_name: str) -> list[str]:
    """Return a copy of active hats with the named hat removed."""
    return [name for name in active_hats if name != hat_name]


def warn_stale_hats(
    active_hats: list[str],
    rounds_active: dict[str, int],
    max_rounds: int = 5,
) -> list[str]:
    """Return active hat names whose round count exceeds max_rounds."""
    return [
        name
        for name in active_hats
        if rounds_active.get(name, 0) > max_rounds
    ]


def inject_hats(prompt: str, active_hats: list[str]) -> str:
    """Prepend active hat content to prompt in order."""
    if not active_hats:
        return prompt
    sections: list[str] = []
    for name in active_hats:
        content = _HAT_CACHE.get(name)
        if content is None:
            continue
        sections.append(f"[Hat: {name}]\n{content}\n[End Hat: {name}]")
    if not sections:
        return prompt
    return "\n\n".join(sections) + "\n\n" + prompt

"""
hat_engine.py
-------------
Discovers hat .md files in agent/hats/, provides tool definitions for each
hat (use/drop), and assembles hat injections for prompt rounds.

Imported by archie_loop.py. Has no dependencies on archie_loop or
archie_memory - it only reads the filesystem.
"""

from __future__ import annotations

from pathlib import Path


_HATS_DIR = Path(__file__).parent / "hats"


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
    """
    Scans agent/hats/ and returns {hat_name: markdown_content}.
    hat_name is the filename stem (e.g. "critic" for critic.md).
    Called once at module import time; result is module-level cached.
    """
    return dict(_HAT_CACHE)


def get_hat_tool_definitions() -> list[dict]:
    """
    Returns a list of tool-call schema dicts for every discovered hat.
    Each hat produces use_hat_{name} and drop_hat_{name} tool definitions.
    """
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


def inject_hats(prompt: str, active_hats: list[str]) -> str:
    """
    Prepends the content of each active hat to prompt, in order.
    Returns the modified prompt. If active_hats is empty, returns prompt unchanged.
    """
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

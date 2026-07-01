"""Hat discovery, tool definitions, activation helpers, and prompt injection."""

from __future__ import annotations

import re
from pathlib import Path

import yaml as _yaml

from skillforge.protocols import ToolSchema
from skillforge.types import ToolResult


_HATS_DIR = Path(__file__).parent / "hats"
MAX_ACTIVE_HATS = 3


def _hat_path(name: str) -> Path | None:
    path = _HATS_DIR / f"{name}.md"
    if path.is_file():
        return path
    return None


def _parse_hat_file(path: str | Path) -> tuple[dict, dict[str, str], str]:
    """
    Parse a hat markdown file.
    Returns (metadata_dict, sections_dict, full_body).
    metadata_dict: parsed YAML frontmatter (empty dict if none).
    sections_dict: H2 section name -> section body text.
    full_body: everything after the frontmatter delimiter.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            try:
                meta = _yaml.safe_load(text[3:end]) or {}
            except Exception:
                meta = {}
            body = text[end + 4 :].lstrip("\n")

    sections: dict[str, str] = {}
    current = None
    current_lines: list[str] = []
    for line in body.splitlines():
        if re.match(r"^##\s+", line):
            if current is not None:
                sections[current] = "\n".join(current_lines).strip()
            current = line[3:].strip()
            current_lines = []
        else:
            if current is not None:
                current_lines.append(line)
    if current is not None:
        sections[current] = "\n".join(current_lines).strip()

    return meta, sections, body


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


def get_native_hat_tool_schemas() -> list[ToolSchema]:
    """Return native function declarations for every discovered Archie hat."""
    return [
        ToolSchema(
            name=f"use_hat_{name}",
            description=(
                f"Read the {name} expert hat before reasoning further about a relevant task."
            ),
        )
        for name in sorted(_HAT_CACHE)
    ]


async def invoke_native_hat(tool_name: str) -> ToolResult:
    """Return a native hat tool's markdown so the model can use it next round."""
    prefix = "use_hat_"
    if not tool_name.startswith(prefix):
        raise KeyError(tool_name)
    name = tool_name[len(prefix):]
    content = _HAT_CACHE.get(name)
    if content is None:
        raise KeyError(tool_name)
    return ToolResult(
        summary=content,
        status="ok",
        data={"hat": name, "content": content},
    )


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


def build_expert_block(name: str) -> str:
    """
    Build the [ACTIVE EXPERT: {display_name} v{version}] injection block
    for the named hat. Returns empty string if hat not found.
    """
    path = _hat_path(name)
    if path is None:
        return ""
    meta, sections, _ = _parse_hat_file(path)
    display = meta.get("display_name", name.replace("_", " ").title())
    version = meta.get("version", "1.0")
    lines = [f"[ACTIVE EXPERT: {display} v{version}]", ""]
    for section in (
        "Persona",
        "Deep Expert Reasoning Style",
        "Expert Instincts",
        "Core Principles",
        "Quality Bar",
        "Pre-Action Checklist",
        "Post-Action Review",
        "Output Contract",
        "Critic Evaluation Guidance",
        "Failure Questions",
    ):
        if section in sections:
            lines += [f"## {section}", sections[section], ""]
    lines.append(f"[End ACTIVE EXPERT: {display}]")
    return "\n".join(lines)


def get_hat_meta(name: str) -> dict:
    """Return parsed frontmatter metadata for the named hat."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return dict(meta)


def get_hat_rules(name: str) -> dict:
    """Return hat_rules metadata for the named hat."""
    meta = get_hat_meta(name)
    rules = meta.get("hat_rules", {})
    return dict(rules) if isinstance(rules, dict) else {}


def get_memory_focus(name: str) -> dict:
    """Return memory_focus metadata for the named hat."""
    meta = get_hat_meta(name)
    focus = meta.get("memory_focus", {})
    return dict(focus) if isinstance(focus, dict) else {}


def get_coordination_rules(name: str) -> dict:
    """Return the coordination dict from the named hat's frontmatter, or {}."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return meta.get("coordination", {})


def get_parallel_hats(name: str) -> list[str]:
    """Return hats that can run in parallel with the named hat."""
    rules = get_coordination_rules(name)
    return rules.get("parallel_with", [])


def get_handoff_message(name: str) -> str | None:
    rules = get_coordination_rules(name)
    return rules.get("handoff_message") or None


def build_memory_view_block(name: str, memory_snapshot) -> str:
    """
    Build a labelled memory view block for injection into the user prompt.

    If memory_snapshot is None or empty, returns an empty string.
    If include_full_memory is True, returns the full snapshot with label.
    Otherwise, returns priority_fields only.
    """
    if memory_snapshot is None:
        return ""

    focus = get_memory_focus(name)
    if not focus:
        return ""

    display = get_hat_meta(name).get("display_name", name)
    priority = focus.get("priority_fields", [])
    include_full = focus.get("include_full_memory", False)
    emphasis = focus.get("emphasis", "")

    lines = [f"[MEMORY VIEW FOR {display.upper()}]"]
    if emphasis:
        lines.append(str(emphasis).strip())
    lines.append("")

    raw = getattr(memory_snapshot, "raw", {}) or {}

    if include_full or not priority:
        if raw:
            for k, v in raw.items():
                lines.append(f"{k}: {v}")
    else:
        found_any = False
        for field in priority:
            for k, v in raw.items():
                if str(field).lower() in str(k).lower() and v:
                    lines.append(f"{k}: {v}")
                    found_any = True
        if not found_any:
            lines.append("(No relevant facts yet recorded for this focus area.)")

    lines.append(f"[End MEMORY VIEW FOR {display.upper()}]")
    return "\n".join(lines)


def get_transition_suggestions(
    active_hats: list[str],
    turn_message: str,
) -> list[dict[str, str]]:
    """Return matching hat activation suggestions for non-active hats."""
    active = set(active_hats)
    suggestions: list[dict[str, str]] = []
    for name in sorted(_HAT_CACHE):
        if name in active:
            continue
        rules = get_hat_rules(name)
        triggers = rules.get("when_to_activate", [])
        if not isinstance(triggers, list):
            continue
        for trigger in triggers:
            if not isinstance(trigger, str):
                continue
            if _trigger_matches(trigger, turn_message):
                suggestions.append({"hat": name, "trigger": trigger})
                break
    return suggestions


def get_suggested_next_hat(name: str) -> str | None:
    """Return the configured next hat for the named hat."""
    suggested = get_hat_rules(name).get("suggested_next_hat")
    return suggested if isinstance(suggested, str) and suggested else None


def _trigger_matches(trigger: str, turn_message: str) -> bool:
    trigger_norm = trigger.lower()
    message_norm = turn_message.lower()
    if trigger_norm in message_norm:
        return True
    message_tokens = set(re.findall(r"[a-z0-9]+", message_norm))
    return any(token in message_tokens for token in _trigger_tokens(trigger_norm))


def _trigger_tokens(trigger: str) -> list[str]:
    stopwords = {
        "about",
        "after",
        "and",
        "any",
        "are",
        "asks",
        "being",
        "call",
        "has",
        "have",
        "into",
        "next",
        "or",
        "out",
        "output",
        "present",
        "requested",
        "requests",
        "result",
        "the",
        "user",
        "when",
        "with",
    }
    return [
        token
        for token in re.findall(r"[a-z0-9]+", trigger)
        if len(token) >= 3 and token not in stopwords
    ]


class HatEngine:
    """Object wrapper around the module-level hat engine API."""

    def get_hat_tool_definitions(self) -> list[dict]:
        return get_hat_tool_definitions()

    def apply_hat(self, active_hats: list[str], hat_name: str) -> list[str]:
        return apply_hat(active_hats, hat_name)

    def drop_hat(self, active_hats: list[str], hat_name: str) -> list[str]:
        return drop_hat(active_hats, hat_name)

    def warn_stale_hats(
        self,
        active_hats: list[str],
        rounds_active: dict[str, int],
        max_rounds: int = 5,
    ) -> list[str]:
        return warn_stale_hats(active_hats, rounds_active, max_rounds)

    def inject_hats(self, prompt: str, active_hats: list[str]) -> str:
        return inject_hats(prompt, active_hats)

    def build_expert_block(self, name: str) -> str:
        return build_expert_block(name)

    def get_hat_meta(self, name: str) -> dict:
        return get_hat_meta(name)

    def get_hat_rules(self, name: str) -> dict:
        return get_hat_rules(name)

    def get_memory_focus(self, name: str) -> dict:
        return get_memory_focus(name)

    def get_coordination_rules(self, name: str) -> dict:
        return get_coordination_rules(name)

    def get_parallel_hats(self, name: str) -> list[str]:
        return get_parallel_hats(name)

    def get_handoff_message(self, name: str) -> str | None:
        return get_handoff_message(name)

    def build_memory_view_block(self, name: str, memory_snapshot) -> str:
        return build_memory_view_block(name, memory_snapshot)

    def get_transition_suggestions(
        self,
        active_hats: list[str],
        turn_message: str,
    ) -> list[dict[str, str]]:
        return get_transition_suggestions(active_hats, turn_message)

    def get_suggested_next_hat(self, name: str) -> str | None:
        return get_suggested_next_hat(name)

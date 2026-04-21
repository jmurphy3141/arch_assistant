from __future__ import annotations

import re
from pathlib import Path


_TOOL_SKILL_MAP = {
    "generate_pov": "oci_customer_pov_writer",
    "generate_terraform": "terraform_for_oci",
}


def skill_name_for_tool(tool_name: str) -> str:
    return _TOOL_SKILL_MAP.get(tool_name, "")


def load_skill(skill_name: str, *, skill_root: Path | None = None) -> str:
    """
    Lightweight skill loader.

    Looks for: <repo>/gstack_skills/<skill_name>/SKILL.md
    Returns empty string when missing or unreadable.
    """
    if not skill_name:
        return ""
    root = skill_root or (Path(__file__).resolve().parents[1] / "gstack_skills")
    skill_path = root / skill_name / "SKILL.md"
    try:
        raw = skill_path.read_text(encoding="utf-8").strip()
        _, body = _parse_frontmatter(raw)
        return body
    except Exception:
        return ""


def load_skill_frontmatter(skill_name: str, *, skill_root: Path | None = None) -> dict[str, str]:
    if not skill_name:
        return {}
    root = skill_root or (Path(__file__).resolve().parents[1] / "gstack_skills")
    skill_path = root / skill_name / "SKILL.md"
    try:
        raw = skill_path.read_text(encoding="utf-8").strip()
        meta, _ = _parse_frontmatter(raw)
        return meta
    except Exception:
        return {}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """
    Minimal YAML-frontmatter parser for simple `key: value` lines.
    """
    if not text.startswith("---\n"):
        return {}, text
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.DOTALL)
    if not match:
        return {}, text
    header, body = match.group(1), match.group(2).strip()
    data: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data, body

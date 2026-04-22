from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any


_TOOL_SKILL_MAP = {
    "generate_pov": "oci_customer_pov_writer",
    "generate_terraform": "terraform_for_oci",
}


@dataclass(frozen=True)
class SkillSpec:
    name: str
    body: str
    metadata: dict[str, str]


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


def discover_skills(*, skill_root: Path | None = None) -> list[SkillSpec]:
    root = skill_root or (Path(__file__).resolve().parents[1] / "gstack_skills")
    out: list[SkillSpec] = []
    if not root.exists():
        return out
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        skill_file = candidate / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            raw = skill_file.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        meta, body = _parse_frontmatter(raw)
        out.append(SkillSpec(name=candidate.name, body=body, metadata=meta))
    return out


def select_skills_for_call(
    *,
    tool_name: str,
    user_message: str = "",
    tool_args: dict[str, Any] | None = None,
    max_skills: int = 2,
    skill_root: Path | None = None,
) -> list[SkillSpec]:
    """
    Dynamically choose the most relevant skills for a specialist call.
    """
    specs = discover_skills(skill_root=skill_root)
    if not specs:
        return []
    args = tool_args or {}
    intent_text = " ".join(
        [
            tool_name or "",
            user_message or "",
            str(args.get("prompt", "") or ""),
            str(args.get("feedback", "") or ""),
        ]
    ).lower()
    intent_tokens = {t for t in re.split(r"[^a-z0-9_]+", intent_text) if len(t) >= 3}

    scored: list[tuple[int, SkillSpec]] = []
    for spec in specs:
        score = _score_skill(spec, tool_name=tool_name, intent_tokens=intent_tokens)
        if score > 0:
            scored.append((score, spec))

    if scored:
        scored.sort(key=lambda it: (-it[0], it[1].name))
        return [spec for _, spec in scored[:max_skills]]

    # Hard fallback for backward compatibility.
    fallback = skill_name_for_tool(tool_name)
    if fallback:
        for spec in specs:
            if spec.name == fallback:
                return [spec]
    return []


def _score_skill(spec: SkillSpec, *, tool_name: str, intent_tokens: set[str]) -> int:
    score = 0
    name_lc = spec.name.lower()
    meta = {k.lower(): v for k, v in (spec.metadata or {}).items()}

    tool_tags_raw = meta.get("tool") or meta.get("tool_tags") or ""
    tool_tags = {t.strip() for t in tool_tags_raw.split(",") if t.strip()}
    if tool_name in tool_tags:
        score += 100

    profile = (meta.get("model_profile") or "").strip().lower()
    if profile:
        if profile in tool_name:
            score += 40
        if profile in {"terraform", "pov"} and profile in name_lc:
            score += 30

    if tool_name == "generate_terraform" and any(
        key in name_lc for key in ("terraform", "plan", "review", "qa", "cso")
    ):
        score += 35
    if tool_name == "generate_pov" and any(
        key in name_lc for key in ("pov", "writer", "customer")
    ):
        score += 35

    keywords_raw = meta.get("keywords", "")
    keyword_tokens = {t.strip().lower() for t in keywords_raw.split(",") if t.strip()}
    overlap = len(intent_tokens & keyword_tokens)
    score += min(overlap * 5, 30)

    # Small semantic fallback from skill name/body.
    body_tokens = {
        tok
        for tok in re.split(r"[^a-z0-9_]+", (spec.body or "").lower())
        if len(tok) >= 4
    }
    semantic_overlap = len(intent_tokens & body_tokens)
    score += min(semantic_overlap, 20)
    return score


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

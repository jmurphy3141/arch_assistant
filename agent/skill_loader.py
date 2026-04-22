from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


_TOOL_SKILL_MAP = {
    "generate_pov": "oci_customer_pov_writer",
    "generate_terraform": "terraform_for_oci",
    "generate_bom": "oci_bom_expert",
}

_TOOL_HINTS = {
    "generate_diagram": {"diagram", "drawio", "architecture", "topology", "layout"},
    "generate_pov": {"pov", "customer", "writing", "narrative", "case_study", "success"},
    "generate_jep": {"jep", "execution", "plan", "poc", "milestone", "timeline"},
    "generate_waf": {"waf", "well_architected", "review", "security", "reliability"},
    "generate_terraform": {"terraform", "iac", "infrastructure", "code", "oci"},
    "generate_bom": {"bom", "bill", "materials", "pricing", "sku", "cost", "sizing"},
}


@dataclass(frozen=True)
class SkillSpec:
    name: str
    body: str
    metadata: dict[str, Any]
    sections: dict[str, str]


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


def load_skill_frontmatter(skill_name: str, *, skill_root: Path | None = None) -> dict[str, Any]:
    if not skill_name:
        return {}
    root = skill_root or (Path(__file__).resolve().parents[1] / "gstack_skills")
    skill_path = root / skill_name / "SKILL.md"
    try:
        raw = skill_path.read_text(encoding="utf-8").strip()
        meta, _ = _parse_frontmatter(raw)
        return _normalize_metadata(meta, skill_name=skill_name)
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
        out.append(
            SkillSpec(
                name=candidate.name,
                body=body,
                metadata=_normalize_metadata(meta, skill_name=candidate.name),
                sections=_extract_sections(body),
            )
        )
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
            str(args.get("bom_text", "") or ""),
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

    hints = _TOOL_HINTS.get(tool_name, {tool_name.replace("generate_", "")})
    tags = _as_token_set(meta.get("tags"))
    tool_tags = _as_token_set(meta.get("tool_tags")) | _as_token_set(meta.get("tool"))
    keywords = _as_token_set(meta.get("keywords"))

    if tool_name in tool_tags:
        score += 150
    if hints & tool_tags:
        score += 80
    if hints & tags:
        score += 50

    profile = str(meta.get("model_profile", "") or "").strip().lower()
    if profile:
        if profile in hints:
            score += 45
        if profile in name_lc:
            score += 30

    if any(hint in name_lc for hint in hints):
        score += 25

    keyword_overlap = len(intent_tokens & keywords)
    score += min(keyword_overlap * 8, 40)

    # Small semantic fallback from skill body.
    body_tokens = {
        tok
        for tok in re.split(r"[^a-z0-9_]+", (spec.body or "").lower())
        if len(tok) >= 4
    }
    semantic_overlap = len(intent_tokens & body_tokens)
    score += min(semantic_overlap * 2, 40)

    # Favor skills with explicit structure; useful for section traceability.
    if spec.sections:
        score += 5
    return score


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    YAML-frontmatter parser with a simple fallback for key:value lines.
    """
    if not text.startswith("---\n"):
        return {}, text
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.DOTALL)
    if not match:
        return {}, text
    header, body = match.group(1), match.group(2).strip()
    try:
        loaded = yaml.safe_load(header)
        if isinstance(loaded, dict):
            return loaded, body
    except Exception:
        pass

    data: dict[str, Any] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data, body


def _normalize_metadata(meta: dict[str, Any], *, skill_name: str) -> dict[str, Any]:
    raw = {str(k).strip().lower(): v for k, v in (meta or {}).items() if str(k).strip()}

    model_profile = str(raw.get("model_profile", "") or "").strip().lower()
    tags = _as_list(raw.get("tags"))
    tool_tags = _as_list(raw.get("tool_tags"))
    if not tool_tags and raw.get("tool"):
        tool_tags = _as_list(raw.get("tool"))

    normalized = {
        "name": str(raw.get("name") or skill_name).strip(),
        "description": str(raw.get("description") or "").strip(),
        "version": str(raw.get("version") or "").strip(),
        "model_profile": model_profile,
        "tags": tags,
        "tool_tags": tool_tags,
        "keywords": _as_list(raw.get("keywords")),
    }
    return normalized


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1]
            parts = [p.strip().strip('"').strip("'") for p in inner.split(",")]
            return [p.lower() for p in parts if p]
        return [p.strip().lower() for p in value.split(",") if p.strip()]
    return []


def _as_token_set(value: Any) -> set[str]:
    return set(_as_list(value))


def _extract_sections(text: str) -> dict[str, str]:
    pattern = re.compile(r"^#{1,3}\s+(.+?)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(text or ""))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections

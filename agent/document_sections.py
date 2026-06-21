"""
agent/document_sections.py
---------------------------
Deterministic section parsing for uploaded customer documents (RFPs, specs,
meeting notes). No LLM involved: section structure is a formatting signal,
and citation verification needs to be deterministic to be trustworthy.

Used by agent/tools/notes.py (list_documents, get_document_section) to let
Archie retrieve verbatim, section-addressable text instead of only the
480-char engagement summary produced by archie_memory._record_saved_note_context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBERED_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[\.\)]?\s+(.{3,120})\s*$")
_MARKDOWN_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LOOSE_TITLE_RE = re.compile(r"^[A-Z][A-Za-z0-9 ,'&/\-]{2,80}$")


@dataclass
class DocSection:
    id: str
    number: str
    title: str
    start_line: int
    end_line: int  # exclusive
    level: int


def parse_sections(text: str) -> list[DocSection]:
    """
    Parse a document into sections using, in order: numbered headings
    ("4.2 Data Residency"), markdown headings ("## Data Residency"), and a
    loose heuristic for standalone Title-Case/ALL-CAPS lines followed by a
    blank line (unnumbered RFP-style section titles, level=0, lower
    confidence). Falls back to a single synthetic section spanning the whole
    document if nothing is detected, so retrieval never hard-fails.
    """
    lines = text.splitlines()
    headings: list[tuple[int, str, str, int]] = []  # (line_no, number, title, level)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        m = _NUMBERED_RE.match(line)
        if m:
            number, title = m.group(1), m.group(2).strip()
            level = number.count(".") + 1
            headings.append((i, number, title, level))
            continue

        m = _MARKDOWN_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append((i, "", title, level))
            continue

        is_short_line = len(stripped) <= 80
        next_blank = (i + 1 >= len(lines)) or (not lines[i + 1].strip())
        prev_blank = (i == 0) or (not lines[i - 1].strip())
        looks_like_title = stripped.isupper() or bool(_LOOSE_TITLE_RE.match(stripped))
        if is_short_line and next_blank and prev_blank and looks_like_title:
            headings.append((i, "", stripped, 0))

    if not headings:
        return [
            DocSection(
                id="full",
                number="",
                title="(unstructured document)",
                start_line=0,
                end_line=len(lines),
                level=1,
            )
        ]

    sections: list[DocSection] = []
    for idx, (line_no, number, title, level) in enumerate(headings):
        end_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        section_id = number or f"s{idx + 1}"
        sections.append(
            DocSection(
                id=section_id,
                number=number,
                title=title,
                start_line=line_no,
                end_line=end_line,
                level=level,
            )
        )
    return sections


def get_section_text(text: str, section: DocSection) -> str:
    """Return the verbatim text spanned by a section (heading line through next heading)."""
    lines = text.splitlines()
    return "\n".join(lines[section.start_line:section.end_line]).strip()


def build_toc(sections: list[DocSection]) -> str:
    """Render a compact table of contents: one line per section."""
    lines = []
    for s in sections:
        label = f"{s.number} {s.title}".strip() if s.number else s.title
        lines.append(f"[{s.id}] {label}")
    return "\n".join(lines)


def find_section(sections: list[DocSection], ref: str) -> DocSection | None:
    """
    Resolve a section reference to a DocSection. Tries, in order: exact id
    match, exact number match, "Section X" / "Section X.Y" phrasing, and a
    case-insensitive title substring match.
    """
    ref = ref.strip()
    if not ref:
        return None

    for s in sections:
        if s.id == ref:
            return s
    for s in sections:
        if s.number and s.number == ref:
            return s

    m = re.match(r"^(?:section|sec\.?|§)\s*([\d.]+)$", ref, re.IGNORECASE)
    if m:
        number = m.group(1)
        for s in sections:
            if s.number == number:
                return s

    ref_lower = ref.lower()
    for s in sections:
        if s.title and ref_lower in s.title.lower():
            return s

    return None

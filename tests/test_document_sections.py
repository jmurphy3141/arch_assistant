from agent.document_sections import (
    build_toc,
    find_section,
    get_section_text,
    parse_sections,
)


NUMBERED_TEXT = (
    "1 Introduction\n"
    "This RFP covers cloud migration.\n"
    "\n"
    "4.2 Data Residency Requirements\n"
    "All customer data must remain in-region.\n"
    "\n"
    "4.3 Security Requirements\n"
    "Encryption at rest is mandatory.\n"
)

MARKDOWN_TEXT = (
    "## Overview\n"
    "Intro paragraph.\n"
    "\n"
    "## Requirements\n"
    "Requirement details here.\n"
)


def test_parse_numbered_headings():
    sections = parse_sections(NUMBERED_TEXT)
    numbers = [s.number for s in sections]
    titles = [s.title for s in sections]
    assert numbers == ["1", "4.2", "4.3"]
    assert titles == ["Introduction", "Data Residency Requirements", "Security Requirements"]
    assert sections[1].level == 2


def test_parse_markdown_headings():
    sections = parse_sections(MARKDOWN_TEXT)
    assert [s.title for s in sections] == ["Overview", "Requirements"]
    assert all(s.level == 2 for s in sections)


def test_parse_unstructured_falls_back_to_single_section():
    text = "Just some plain text.\nNo headings here at all.\n"
    sections = parse_sections(text)
    assert len(sections) == 1
    assert sections[0].id == "full"


def test_get_section_text_boundaries():
    sections = parse_sections(NUMBERED_TEXT)
    data_residency = next(s for s in sections if s.number == "4.2")
    text = get_section_text(NUMBERED_TEXT, data_residency)
    assert "Data Residency Requirements" in text
    assert "All customer data must remain in-region." in text
    assert "Security Requirements" not in text


def test_build_toc_includes_numbers_and_titles():
    sections = parse_sections(NUMBERED_TEXT)
    toc = build_toc(sections)
    assert "4.2 Data Residency Requirements" in toc
    assert "4.3 Security Requirements" in toc


def test_find_section_by_number():
    sections = parse_sections(NUMBERED_TEXT)
    found = find_section(sections, "4.2")
    assert found is not None
    assert found.title == "Data Residency Requirements"


def test_find_section_by_section_phrasing():
    sections = parse_sections(NUMBERED_TEXT)
    found = find_section(sections, "Section 4.2")
    assert found is not None
    assert found.number == "4.2"


def test_find_section_by_title_substring():
    sections = parse_sections(NUMBERED_TEXT)
    found = find_section(sections, "data residency")
    assert found is not None
    assert found.number == "4.2"


def test_find_section_no_match_returns_none():
    sections = parse_sections(NUMBERED_TEXT)
    assert find_section(sections, "9.9") is None

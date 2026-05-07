import pytest
from skillforge.types import MemorySnapshot, ToolResult


def _make_forge():
    import agent.hat_engine as hat_engine
    from skillforge import Forge, SimpleMemory

    async def _runner(p, s, l=""):
        return "ok"

    return Forge(
        base_system_prompt="original prompt",
        hat_engine=hat_engine,
        memory=SimpleMemory(),
        text_runner=_runner,
    )


def test_set_base_prompt_file_loads_content(tmp_path):
    """set_base_prompt_file replaces the base system prompt from a file."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a specialist agent for AWS.")

    forge = _make_forge()
    forge.set_base_prompt_file(str(prompt_file))
    assert "AWS" in forge._base_system_prompt


def test_set_base_prompt_file_invalidates_cache(tmp_path):
    """Changing the prompt file resets the cached system message."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("Prompt v1")

    forge = _make_forge()
    _ = forge._get_system_msg()  # populate cache
    assert forge._system_msg is not None

    prompt_file.write_text("Prompt v2")
    forge.set_base_prompt_file(str(prompt_file))
    assert forge._system_msg is None  # cache cleared


def test_skill_guidance_loaded_from_file(tmp_path):
    """register_tool reads skill_guidance content from a file path."""
    from unittest.mock import AsyncMock

    skill_file = tmp_path / "my_skill.md"
    skill_file.write_text("## Guidance\nAlways check region.")

    forge = _make_forge()
    forge.register_tool(
        "guided_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        skill_guidance=str(skill_file),
    )
    spec = forge._registry.get("guided_tool")
    assert "Always check region" in spec.skill_guidance


def test_skill_guidance_inline_string_unchanged(tmp_path):
    """Inline markdown strings are not treated as file paths."""
    from unittest.mock import AsyncMock

    forge = _make_forge()
    inline = "## Guidance\nBe precise."
    forge.register_tool(
        "inline_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        skill_guidance=inline,
    )
    spec = forge._registry.get("inline_tool")
    assert spec.skill_guidance == inline


def test_skill_guidance_nonexistent_path_kept_as_string(tmp_path):
    """A string that looks like a path but doesn't exist is kept as-is."""
    from unittest.mock import AsyncMock

    forge = _make_forge()
    fake_path = str(tmp_path / "does_not_exist.md")
    forge.register_tool(
        "fake_path_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        skill_guidance=fake_path,
    )
    spec = forge._registry.get("fake_path_tool")
    assert spec.skill_guidance == fake_path

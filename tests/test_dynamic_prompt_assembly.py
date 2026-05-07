import pytest
from skillforge.types import MemorySnapshot, ToolResult
from unittest.mock import AsyncMock


def _make_forge(base_prompt: str = "Base prompt."):
    import agent.hat_engine as hat_engine
    from skillforge import Forge, SimpleMemory

    async def _runner(p, s, l=""):
        return "ok"

    return Forge(
        base_system_prompt=base_prompt,
        hat_engine=hat_engine,
        memory=SimpleMemory(),
        text_runner=_runner,
    )


def test_global_skill_in_system_prompt(tmp_path):
    """register_skill_file content appears in the assembled system prompt."""
    skill = tmp_path / "routing.md"
    skill.write_text("## Routing Rules\nAlways check region first.")

    forge = _make_forge()
    forge.register_skill_file(str(skill))
    msg = forge._get_system_msg()

    assert "Always check region first" in msg


def test_multiple_global_skills_all_present(tmp_path):
    """Multiple registered skill files all appear in the system prompt."""
    (tmp_path / "s1.md").write_text("Skill one content.")
    (tmp_path / "s2.md").write_text("Skill two content.")

    forge = _make_forge()
    forge.register_skill_file(str(tmp_path / "s1.md"))
    forge.register_skill_file(str(tmp_path / "s2.md"))
    msg = forge._get_system_msg()

    assert "Skill one content" in msg
    assert "Skill two content" in msg


def test_tool_skill_guidance_in_system_prompt(tmp_path):
    """Tool-level skill_guidance appears in the assembled system prompt."""
    skill = tmp_path / "bom.md"
    skill.write_text("## BOM Guidance\nAlways include storage tier.")

    forge = _make_forge()
    forge.register_tool(
        "generate_bom",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        skill_guidance=str(skill),
    )
    msg = forge._get_system_msg()

    assert "BOM Guidance" in msg or "Always include storage tier" in msg


def test_register_skill_file_invalidates_cache(tmp_path):
    """Registering a skill file after cache is built resets the cache."""
    skill = tmp_path / "s.md"
    skill.write_text("Late skill.")

    forge = _make_forge()
    _ = forge._get_system_msg()  # populate cache
    assert forge._system_msg is not None

    forge.register_skill_file(str(skill))
    assert forge._system_msg is None  # cache cleared
    assert "Late skill" in forge._get_system_msg()


def test_format_instruction_still_present(tmp_path):
    """The tool call format instruction from p2a0 is not displaced."""
    skill = tmp_path / "s.md"
    skill.write_text("Some global skill.")

    forge = _make_forge()
    forge.register_skill_file(str(skill))
    forge.register_tool(
        "my_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
    )
    msg = forge._get_system_msg()

    assert '{"tool"' in msg
    assert "Tool call format" in msg


def test_no_skills_prompt_unchanged():
    """With no skill files registered, the existing prompt assembly is unaffected."""
    forge = _make_forge("Just the base.")
    msg = forge._get_system_msg()
    assert "Just the base" in msg
    assert '{"tool"' in msg


def test_system_prompt_cached_after_skill_registration(tmp_path):
    """After registering skills and building once, subsequent calls return same object."""
    skill = tmp_path / "s.md"
    skill.write_text("Cached skill.")

    forge = _make_forge()
    forge.register_skill_file(str(skill))
    msg1 = forge._get_system_msg()
    msg2 = forge._get_system_msg()
    assert msg1 is msg2

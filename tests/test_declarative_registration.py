import pytest
from unittest.mock import AsyncMock, MagicMock
from skillforge.types import ToolResult


def _make_bare_forge():
    import agent.hat_engine as hat_engine
    from skillforge import Forge, SimpleMemory

    async def _runner(p, s, l=""):
        return "ok"

    return Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=SimpleMemory(),
        text_runner=_runner,
    )


def test_register_from_dict_basic():
    """register_tools_from_config accepts a dict and registers tools."""
    forge = _make_bare_forge()

    # Point handler at a known importable callable
    config = {
        "tools": [
            {
                "name": "my_tool",
                "handler": "skillforge.memory:SimpleMemory",
                "memory_contract": False,
            }
        ]
    }
    forge.register_tools_from_config(config)
    assert forge._registry.get("my_tool") is not None


def test_register_from_dict_memory_contract():
    """memory_contract flag is respected."""
    forge = _make_bare_forge()
    config = {
        "tools": [
            {
                "name": "mem_tool",
                "handler": "skillforge.memory:SimpleMemory",
                "memory_contract": True,
            }
        ]
    }
    forge.register_tools_from_config(config)
    assert forge._registry.requires_memory("mem_tool") is True


def test_register_from_dict_critique_enabled():
    """critique_enabled flag is respected."""
    forge = _make_bare_forge()
    config = {
        "tools": [
            {
                "name": "critic_tool",
                "handler": "skillforge.memory:SimpleMemory",
                "critique_enabled": True,
            }
        ]
    }
    forge.register_tools_from_config(config)
    spec = forge._registry.get("critic_tool")
    assert spec.critique_enabled is True


def test_register_from_yaml_file(tmp_path):
    """register_tools_from_config accepts a YAML file path."""
    yaml_content = """
tools:
  - name: file_tool
    handler: skillforge.memory:SimpleMemory
    memory_contract: false
"""
    config_file = tmp_path / "tools.yaml"
    config_file.write_text(yaml_content)

    forge = _make_bare_forge()
    forge.register_tools_from_config(str(config_file))
    assert forge._registry.get("file_tool") is not None


def test_register_skill_guidance_from_file(tmp_path):
    """skill_guidance path is resolved and read from file."""
    skill_md = tmp_path / "my_skill.md"
    skill_md.write_text("## Guidance\nBe precise.")

    config = {
        "tools": [
            {
                "name": "guided_tool",
                "handler": "skillforge.memory:SimpleMemory",
                "skill_guidance": "my_skill.md",
            }
        ]
    }
    forge = _make_bare_forge()
    forge.register_tools_from_config(config, base_dir=str(tmp_path))
    spec = forge._registry.get("guided_tool")
    assert "Be precise" in spec.skill_guidance


def test_import_symbol_bad_path():
    """_import_symbol raises ValueError for missing colon."""
    from skillforge.forge import _import_symbol
    with pytest.raises(ValueError, match="module:symbol"):
        _import_symbol("no_colon_here")

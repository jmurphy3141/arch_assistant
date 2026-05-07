# Task p35a: Declarative Tool Registration via YAML Config

## Goal

Add `Forge.register_tools_from_config()` — a method that registers tools from
a YAML file or dict. Teams can describe their entire tool set in config without
writing per-tool Python wiring. The existing `register_tool()` method is
unchanged; this is additive.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass. Also confirm PyYAML is available:
```bash
python3.11 -c "import yaml; print('ok')"
```

If yaml is missing, add `PyYAML` to `requirements.txt` and install it.

---

## Scope

**Only modify:**
- `skillforge/forge.py` — add `register_tools_from_config()`

**Only create:**
- `tests/test_declarative_registration.py`
- `examples/sample_tools.yaml` — minimal example config

**Do NOT modify `archie_wiring.py` yet — that migration is a follow-on task.**

---

## Config format

```yaml
# forge_tools.yaml
tools:
  - name: save_notes
    handler: agent.tools.notes:NotesHandlers
    memory_contract: false

  - name: generate_bom
    handler: agent.tools.bom:BomHandler
    memory_contract: true
    critique_enabled: true
    skill_guidance: skills/bom_guidance.md   # optional: path to .md file

  - name: generate_diagram
    handler: skillforge.delegate:A2ADelegate
    handler_kwargs:                           # passed to handler constructor
      base_url: "http://localhost:8081"
      endpoint: "/generate/diagram"
    memory_contract: true
```

### Field reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | str | yes | Tool name registered with Forge |
| `handler` | str | yes | `"module.path:ClassName"` — imported and instantiated |
| `handler_kwargs` | dict | no | Kwargs passed to handler constructor |
| `memory_contract` | bool | no | Default false |
| `critique_enabled` | bool | no | Default false |
| `skill_guidance` | str | no | Inline string or path to .md file |
| `safety_checker` | str | no | `"module:function"` — imported callable |

---

## What to implement in `skillforge/forge.py`

Add the following method to the `Forge` class:

```python
def register_tools_from_config(
    self,
    config: str | dict,
    *,
    base_dir: str | None = None,
) -> None:
    """
    Register tools from a YAML file path or a dict.

    Parameters
    ----------
    config   : Path to a YAML file, or a dict already parsed from YAML.
    base_dir : Base directory for resolving relative skill_guidance paths.
               Defaults to the directory containing the config file (if path
               given) or the current working directory.
    """
    import importlib
    import os
    import yaml

    if isinstance(config, str):
        config_path = config
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(config_path))
        with open(config_path) as f:
            data = yaml.safe_load(f)
    else:
        data = config
        if base_dir is None:
            base_dir = os.getcwd()

    for tool_cfg in data.get("tools", []):
        name = tool_cfg["name"]
        handler = _import_symbol(tool_cfg["handler"])
        handler_kwargs = tool_cfg.get("handler_kwargs") or {}
        if handler_kwargs:
            handler = handler(**handler_kwargs)

        # Resolve skill_guidance
        skill_guidance = tool_cfg.get("skill_guidance", "")
        if skill_guidance and os.path.exists(os.path.join(base_dir, skill_guidance)):
            with open(os.path.join(base_dir, skill_guidance)) as f:
                skill_guidance = f.read()

        # Resolve safety_checker
        safety_checker = None
        if tool_cfg.get("safety_checker"):
            safety_checker = _import_symbol(tool_cfg["safety_checker"])

        self.register_tool(
            name,
            handler,
            memory_contract=bool(tool_cfg.get("memory_contract", False)),
            critique_enabled=bool(tool_cfg.get("critique_enabled", False)),
            skill_guidance=skill_guidance or "",
            safety_checker=safety_checker,
        )


def _import_symbol(dotted_path: str) -> Any:
    """Import 'package.module:ClassName' or 'package.module:function'."""
    if ":" not in dotted_path:
        raise ValueError(f"handler must be 'module:symbol', got: {dotted_path!r}")
    module_path, symbol = dotted_path.rsplit(":", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, symbol)
```

Place `_import_symbol` as a module-level function at the bottom of `forge.py`
(after `_assemble_system_prompt`).

---

## `examples/sample_tools.yaml`

```yaml
# Minimal example showing declarative tool registration.
# Used by the quickstart — not imported by any production code.
tools:
  - name: save_notes
    handler: skillforge.memory:SimpleMemory   # placeholder — replace with real handler
    memory_contract: false

  - name: my_tool
    handler: skillforge.memory:SimpleMemory   # placeholder
    memory_contract: true
    critique_enabled: false
```

---

## Test: `tests/test_declarative_registration.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `pytest tests/test_declarative_registration.py -v` — 6 passed
3. `pytest tests/test_forge.py -v` — no regressions (14 passed)
4. `examples/sample_tools.yaml` exists and is valid YAML
5. `grep "register_tools_from_config" skillforge/forge.py` — matches

---

## Do NOT Do

- Do not change the `register_tool()` signature
- Do not modify `archie_wiring.py` — migration is a follow-on task
- Do not make `handler_kwargs` support positional args — kwargs only

---

## Commit Message

```
p35a: add Forge.register_tools_from_config() — declarative YAML-based tool registration
```

# Task p35b: Skill Files — Externalize Guidance and Base Prompt to .md Files

## Goal

Two changes that move Archie toward prompt-first development:

1. `Forge.set_base_prompt_file(path)` — load the orchestrator system prompt
   from a `.md` file instead of a hardcoded Python string
2. `register_tool(..., skill_guidance="path/to/file.md")` — accept a file path
   for skill_guidance in addition to an inline string

Both changes are backward-compatible. Existing string-based usage still works.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass.

---

## Scope

**Only modify:**
- `skillforge/forge.py` — add `set_base_prompt_file()`, extend `register_tool()`

**Only create:**
- `tests/test_skill_files.py`
- `skills/` directory with `README.md` (one-liner explaining the directory)

**Do NOT modify `archie_wiring.py` or any hat file yet.**

---

## What to implement

### In `skillforge/forge.py`

#### Add `set_base_prompt_file()` to `Forge`

```python
def set_base_prompt_file(self, path: str) -> None:
    """
    Load the base system prompt from a .md file, replacing any previously
    set base_system_prompt. Invalidates the cached system message.

    Call before the first run_turn(). Calling after turns have started
    resets the cache — the new prompt takes effect on the next turn.
    """
    with open(path) as f:
        self._base_system_prompt = f.read()
    self._system_msg = None  # invalidate cache
```

#### Extend `register_tool()` — resolve skill_guidance path

In `register_tool()`, before passing `skill_guidance` to the registry, add:

```python
# Resolve skill_guidance from file if it looks like a path
if skill_guidance and not skill_guidance.strip().startswith(("#", "\n", " ")):
    import os
    if os.path.isfile(skill_guidance):
        with open(skill_guidance) as f:
            skill_guidance = f.read()
```

The heuristic: if `skill_guidance` does not start with `#`, newline, or space
(i.e. it's not clearly markdown content), check whether it's a readable file
path. If yes, read it. This lets both usages work:
```python
forge.register_tool("my_tool", handler, skill_guidance="skills/bom.md")  # file
forge.register_tool("my_tool", handler, skill_guidance="## Guidance\nBe precise.")  # inline
```

---

## `skills/README.md`

```markdown
# skills/

Skill files are markdown documents that provide per-tool guidance injected
into the LLM prompt before each tool invocation.

Each file should contain:
- A brief description of what the tool does and when to use it
- Constraints or rules the LLM should follow when calling this tool
- Examples of good vs. bad tool arguments (optional)

Register a skill file with:
    forge.register_tool("my_tool", handler, skill_guidance="skills/my_tool.md")

Or via YAML config:
    - name: my_tool
      handler: my_module:MyHandler
      skill_guidance: skills/my_tool.md
```

---

## Test: `tests/test_skill_files.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `pytest tests/test_skill_files.py -v` — 5 passed
3. `pytest tests/test_forge.py -v` — no regressions
4. `ls skills/README.md` — exists
5. `grep "set_base_prompt_file" skillforge/forge.py` — matches

---

## Do NOT Do

- Do not change the default behavior of `register_tool()` for existing callers
- Do not scan directories — resolve one explicit path per tool, no globbing
- Do not create any `.md` files in `skills/` other than `README.md` in this task
  (skill content for specific Archie tools is a follow-on)

---

## Commit Message

```
p35b: add set_base_prompt_file() and file-path skill_guidance — prompt-first authoring
```

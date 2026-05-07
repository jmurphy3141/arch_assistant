# Task p35i: Dynamic System Prompt Assembly from Registered Tools and Skills

## Goal

When `Forge._get_system_msg()` assembles the system prompt, it should
automatically include guidance from registered skill files — not just the base
system prompt and hat tool definitions. The result is that any team registering
tools with `skill_guidance` gets a complete, context-rich system prompt without
manually assembling strings in application code.

Additionally, expose `Forge.register_skill_file(path)` for injecting global
skill content (e.g. routing guidance, safety rules) that applies to the full
session rather than a single tool.

---

## Background

Today `_get_system_msg()` does:
```
base_system_prompt + hat_tool_definitions + tool_list
```

After this task it does:
```
base_system_prompt + global_skill_files + tool_list_with_inline_guidance + hat_tool_definitions
```

The LLM sees the full picture on every turn without the application manually
prepending skill content to `base_system_prompt`.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py skillforge/registry.py skillforge/memory.py
pytest tests/test_forge.py tests/test_skill_files.py tests/test_simple_memory.py -v --tb=short 2>&1 | tail -4
```

All must pass. If `tests/test_skill_files.py` does not exist, p35b is
incomplete — stop.

---

## Scope

**Only modify:**
- `skillforge/forge.py` — add `register_skill_file()`, update `_get_system_msg()`
- `skillforge/forge.py` — update `_assemble_system_prompt()` signature
- `skillforge/memory.py` — fix `SimpleMemory.assemble()` frozen dataclass workaround (see below)

**Only create:**
- `tests/test_dynamic_prompt_assembly.py`

**Do NOT modify `skillforge/registry.py`, `agent/`, or any existing test.**

---

## What to implement

### 1. Add `register_skill_file()` to `Forge`

```python
def register_skill_file(self, path: str) -> None:
    """
    Register a global skill file whose content is injected into the
    system prompt on every turn, before tool-specific guidance.

    Call after construction, before the first run_turn().
    Can be called multiple times — files are appended in order.
    Invalidates the system message cache.

    Parameters
    ----------
    path : Path to a .md file. Read immediately at registration time.
    """
    with open(path) as f:
        content = f.read().strip()
    if content:
        self._global_skills.append(content)
    self._system_msg = None  # invalidate cache
```

Add `self._global_skills: list[str] = []` to `Forge.__init__()`.

---

### 2. Update `_get_system_msg()` to include global skills and tool guidance

```python
def _get_system_msg(self) -> str:
    """Build once after all tools are registered; cache thereafter."""
    if self._system_msg is None:
        hat_tools = self._hat_engine.get_hat_tool_definitions()
        self._system_msg = _assemble_system_prompt(
            self._base_system_prompt,
            hat_tools,
            self._registry,
            global_skills=self._global_skills,
        )
    return self._system_msg
```

---

### 3. Update `_assemble_system_prompt()` to include global skills and tool guidance

Extend the existing function signature and body:

```python
def _assemble_system_prompt(
    base: str,
    hat_tools: list[dict],
    registry: ToolRegistry,
    global_skills: list[str] | None = None,
) -> str:
    parts: list[str] = []

    if base:
        parts.append(base.strip())

    # Global skill files (routing guidance, safety rules, etc.)
    for skill in (global_skills or []):
        if skill.strip():
            parts.append(skill.strip())

    # Tool list with inline skill guidance
    tool_entries = []
    for name, spec in registry._tools.items():
        entry = f"- {name}"
        if spec.description:
            entry += f": {spec.description}"
        if spec.skill_guidance:
            entry += f"\n  Guidance: {spec.skill_guidance.strip()[:300]}"
            if len(spec.skill_guidance) > 300:
                entry += "..."
        tool_entries.append(entry)

    if tool_entries:
        parts.append("Available tools:\n" + "\n".join(tool_entries))

    # Hat tool definitions
    if hat_tools:
        hat_names = [t.get("name", "") for t in hat_tools]
        parts.append("Hat tools (expert lenses): " + ", ".join(hat_names))

    # Format instruction (must always be present — see p2a0)
    parts.append(
        'Tool call format (JSON on a single line):\n{"tool": "<name>", "args": {<key>: <value>}}'
    )

    return "\n\n".join(parts)
```

**Important:** The existing format instruction (`Tool call format`) must remain
in the assembled prompt. Do not remove it. Check that `test_format_instruction_in_system_prompt`
in `test_forge.py` still passes.

---

### 4. Fix `SimpleMemory.assemble()` — remove frozen dataclass workaround

The current implementation in `skillforge/memory.py` uses:
```python
object.__setattr__(snapshot, "artifacts", artifacts)
```
This bypasses the frozen dataclass and is fragile. Fix it by checking the
actual fields on `MemorySnapshot` and using the correct constructor argument.

Run this first to see what fields `MemorySnapshot` accepts:
```bash
python3.11 -c "import inspect; from skillforge.types import MemorySnapshot; print(inspect.signature(MemorySnapshot))"
```

Replace the `object.__setattr__` hack with the proper field name. If
`MemorySnapshot` uses `prior_artifacts` rather than `artifacts`, update
`SimpleMemory.assemble()` to pass it correctly and remove the workaround
entirely. The `test_simple_memory.py` tests (specifically
`test_update_stores_artifact_key` and `test_assemble_empty_session`) must
still pass after the fix.

---

## Test: `tests/test_dynamic_prompt_assembly.py`

```python
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
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py skillforge/memory.py` exits 0
2. `pytest tests/test_dynamic_prompt_assembly.py -v` — 7 passed
3. `pytest tests/test_forge.py -v` — 14 passed, including
   `test_format_instruction_in_system_prompt` and `test_system_prompt_cached`
4. `pytest tests/test_skill_files.py -v` — 5 passed (no regression)
5. `pytest tests/test_simple_memory.py -v` — all passed (no regression)
6. `grep "register_skill_file\|global_skills" skillforge/forge.py` — matches
7. `grep "object.__setattr__" skillforge/memory.py` — no output (workaround removed)
8. `pytest tests/test_specialist_mode_routing.py -v` — 45 passed

---

## Do NOT Do

- Do not change the external behavior of `run_turn()` or `invoke_tool()`
- Do not truncate the base system prompt — only add to it
- Do not make `_assemble_system_prompt` import from `agent/`
- Do not remove the format instruction — `test_format_instruction_in_system_prompt`
  must stay green
- Do not use `object.__setattr__` anywhere in `skillforge/` — if a frozen
  dataclass field needs setting, use the constructor correctly

---

## Commit Message

```
p35i: dynamic system prompt assembly + fix SimpleMemory frozen dataclass workaround
```

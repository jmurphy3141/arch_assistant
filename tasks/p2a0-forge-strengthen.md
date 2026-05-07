# Task p2a0: Strengthen skillforge/forge.py Core

## Goal

Fix two real gaps in `skillforge/forge.py` before tests are written:

1. **Missing tool-call format instruction** — the LLM has no instruction telling it
   to emit `{"tool": "name", "args": {...}}`. Without this, the ReAct loop silently
   falls through to plain reply on every turn. `archie_loop.py` line 102-103 has
   the equivalent. Forge must include it too.

2. **`critique_enabled` flag on register_tool** — reserved for post-tool critic
   review. Add the parameter and `ToolSpec` field now (no-op behavior; the critique
   loop is implemented in a follow-on task). This keeps the register_tool API stable
   across the p2a-p2h series.

## Prerequisite Check

```bash
python3.11 -c "from skillforge import Forge, ToolResult, MemorySnapshot; print('ok')"
```

If this fails, stop and report. Do not proceed.

## Scope

**Only modify these files:**

- `skillforge/forge.py`
- `skillforge/registry.py`

**Do NOT touch:**

- `agent/` — any file
- `tests/` — any file
- `skillforge/types.py`, `skillforge/protocols.py`

## Changes to `skillforge/forge.py`

### 1. Add `_TOOL_CALL_FORMAT_INSTRUCTION` constant

Insert this constant near the top of the file (after the imports):

```python
_TOOL_CALL_FORMAT_INSTRUCTION = (
    "\n\nTool call format — when calling a tool output ONLY this JSON on a single line:\n"
    '{"tool": "<tool_name>", "args": {<key>: <value>}}\n'
    "To reply without calling a tool, output plain prose — no JSON."
)
```

### 2. Append to `_assemble_system_prompt`

In `_assemble_system_prompt`, append the format instruction at the end, always:

```python
def _assemble_system_prompt(
    base: str, hat_tools: list[dict], registry: ToolRegistry | None
) -> str:
    parts = [base.rstrip()]
    if registry:
        tool_block = registry.tool_contract_block()
        if tool_block:
            parts.append("\nTool contracts:\n" + tool_block)
    if hat_tools:
        hat_lines = [
            "- use_hat_X activates an expert hat before the next reasoning round.",
            "- drop_hat_X deactivates an active expert hat.",
        ]
        for tool in hat_tools:
            fn = tool.get("function", {}) if isinstance(tool, dict) else {}
            name = str(fn.get("name") or "").strip()
            if name:
                hat_lines.append(f"- {name} {{}}")
        parts.append("\nHat tools:\n" + "\n".join(hat_lines))
    parts.append(_TOOL_CALL_FORMAT_INSTRUCTION)   # ← always append last
    return "\n".join(parts)
```

### 3. Add `critique_enabled` to `Forge.register_tool`

Add the parameter with default `False`:

```python
def register_tool(
    self,
    name: str,
    handler: Any,
    *,
    description: str = "",
    args_schema: dict[str, str] | None = None,
    memory_contract: bool = False,
    safety_checker: Any | None = None,
    skill_guidance: str = "",
    parallel_safe: bool = False,
    retry_on_needs_input: bool = False,
    critique_enabled: bool = False,   # ← new
) -> None:
    ...
    self._registry.register(
        name,
        handler,
        ...
        critique_enabled=critique_enabled,   # ← pass through
    )
```

No behavior change in the loop yet — just store the flag.

## Changes to `skillforge/registry.py`

### 1. Add `critique_enabled` to `ToolSpec`

```python
@dataclass
class ToolSpec:
    name: str
    handler: ToolHandler
    description: str = ""
    args_schema: dict = field(default_factory=dict)
    memory_contract: bool = False
    safety_checker: SafetyChecker | None = None
    skill_guidance: str = ""
    parallel_safe: bool = False
    retry_on_needs_input: bool = False
    critique_enabled: bool = False   # ← new
```

### 2. Add `critique_enabled` to `ToolRegistry.register`

```python
def register(
    self,
    name: str,
    handler: ToolHandler,
    *,
    ...
    critique_enabled: bool = False,   # ← new
) -> None:
    ...
    self._tools[name] = ToolSpec(
        ...
        critique_enabled=critique_enabled,   # ← new
    )
```

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py skillforge/registry.py` exits 0
2. `python3.11 -c "
from skillforge import Forge, ToolResult, MemorySnapshot
import asyncio

class FakeHat:
    def get_hat_tool_definitions(self): return []
    def apply_hat(self, a, n): return a
    def drop_hat(self, a, n): return a
    def warn_stale_hats(self, a, r, max_rounds=5): return []
    def inject_hats(self, p, a): return p

class FakeMem:
    def assemble(self, *, session_id, context, user_message): return MemorySnapshot(session_id=session_id)
    def update(self, *, session_id, tool_name, result, context): return context

async def runner(p, s, l): return 'hello'

f = Forge(base_system_prompt='test', hat_engine=FakeHat(), memory=FakeMem(), text_runner=runner)
f.register_tool('my_tool', None, critique_enabled=True)
assert 'tool_name' in f._get_system_msg(), 'format instruction missing'
print('ok')
" `
3. `grep "Tool call format" skillforge/forge.py` — matches

## Do NOT Do

- Do not implement the critic loop — just store the flag
- Do not modify `archie_loop.py` or any `agent/` file
- Do not change `ToolHandler`, `Memory`, or any protocol

## Commit Message

```
p2a0: add tool-call format instruction and critique_enabled flag to Forge
```

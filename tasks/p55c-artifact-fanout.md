# Codex Agent Prompt — Issue 3: Parallel Artifact Fan-out After POC Selection

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/explore-repo-Os53i
**Requirements:** docs/requirements-poc-workflow.md FR-3.*
**Depends on:** Issue 2 (PocStrategistHandler must exist)

---

## Task

After the SE confirms which POC option to build, all 5 artifacts (diagram, BOM, JEP, Terraform, presentation) must generate simultaneously via Forge's existing parallel dispatch mechanism.

---

## Context

### How Forge parallel dispatch works (no changes needed)

`skillforge/forge.py` lines 745–786 already handles parallel tool dispatch. When a tool handler returns:

```python
ToolResult(
    status="parallel",
    summary="...",
    parallel_tools=[
        ParallelToolCall(tool="generate_diagram", args={...}),
        ParallelToolCall(tool="generate_bom", args={...}),
        ...
    ]
)
```

Forge calls all tools in `parallel_tools` concurrently via `asyncio.gather()`, collects results, and combines summaries. **Zero changes to forge.py are needed.**

`ParallelToolCall` is defined in `skillforge/types.py`.

### `PocStrategistHandler` (from Issue 2)

`agent/tools/specialists.py` — the handler built in Issue 2. This issue extends `__call__()` to detect confirmation intent and return the fan-out ToolResult.

### Confirmation signals

The user says something like:
- "go with option 1" / "option 2" / "option 3"
- "use the migration approach"
- "let's do the cost optimization one"
- "confirm option 1"
- "proceed with option 2"

Detection should be simple string matching — not an LLM call.

### Memory context

After `generate_poc_plan` runs, `poc_recommendation` is in memory (set via `memory_contract`). The `data` field of the previous ToolResult contains `poc_options` and `recommendation`.

---

## What to Build

### `agent/tools/specialists.py` — Extend `PocStrategistHandler.__call__()`

Add confirmation detection at the top of `__call__()`, before the exploration path:

```python
async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
    user_message = args.get("_user_message", "").lower()

    # Detect POC confirmation
    confirmed_option = _detect_poc_confirmation(user_message, memory)
    if confirmed_option is not None:
        return self._build_fanout_result(confirmed_option, memory)

    # ... existing exploration path below ...
```

**`_detect_poc_confirmation(user_message, memory)`** — module-level helper:

```python
def _detect_poc_confirmation(user_message: str, memory) -> dict | None:
    """Returns the confirmed poc_option dict, or None if no confirmation detected."""
    confirmation_patterns = [
        (r"\boption\s*1\b", 0),
        (r"\boption\s*2\b", 1),
        (r"\boption\s*3\b", 2),
        (r"\bgo\s+with\b", 0),      # defaults to top recommendation
        (r"\bproceed\s+with\b", 0),
        (r"\bconfirm\b", 0),
        (r"\blet'?s\s+do\b", 0),
    ]
    poc_options = []
    if memory and hasattr(memory, "decision_context"):
        poc_options = memory.decision_context.get("poc_options", [])

    if not poc_options:
        return None

    for pattern, index in confirmation_patterns:
        if re.search(pattern, user_message):
            safe_index = min(index, len(poc_options) - 1)
            return poc_options[safe_index]

    return None
```

**`_build_fanout_result(option, memory)`** — method on `PocStrategistHandler`:

```python
def _build_fanout_result(self, option: dict, memory) -> ToolResult:
    poc_name = option.get("option_name", "POC")
    services = option.get("oci_services", [])
    dc = memory.decision_context if memory else {}

    return ToolResult(
        status="parallel",
        summary=f"POC confirmed: {poc_name}. Generating all artifacts in parallel...",
        parallel_tools=[
            ParallelToolCall(
                tool="generate_diagram",
                args={"diagram_name": poc_name.lower().replace(" ", "-")[:40], "_user_message": f"Create OCI architecture diagram for: {poc_name}. Services: {', '.join(services)}"},
            ),
            ParallelToolCall(
                tool="generate_bom",
                args={"_user_message": f"Generate BOM for POC: {poc_name}. Services: {', '.join(services)}. Region: {dc.get('region', 'us-chicago-1')}"},
            ),
            ParallelToolCall(
                tool="generate_jep",
                args={"_user_message": f"Create JEP execution plan for POC: {poc_name}. Build sequence: {option.get('build_sequence', [])}"},
            ),
            ParallelToolCall(
                tool="generate_terraform",
                args={"_user_message": f"Generate Terraform for: {poc_name}. Services: {', '.join(services)}"},
            ),
            ParallelToolCall(
                tool="generate_presentation",
                args={"_user_message": f"Create client PowerPoint deck for POC: {poc_name}", "poc_option": option},
            ),
        ],
    )
```

Add `import re` at the top of `specialists.py` if not already present.
Add `from skillforge.types import ParallelToolCall` if not already imported.

---

## Constraints

- Zero changes to `skillforge/forge.py`
- Confirmation detection uses `re.search()` — not an LLM call
- Each parallel tool's `_user_message` must be self-contained (the tool's handler receives it as context)
- If `poc_options` is absent from memory (user said "confirm" but no plan exists), return `ToolResult(status="needs_input", clarification="Please generate a POC plan first with generate_poc_plan.")`
- Default to option index 0 (the recommended option) for ambiguous confirmations like "go with it" or "let's do it"

---

## Tests

Add to `tests/test_poc_strategist.py`:

```python
async def test_confirmation_returns_parallel_toolresult():
    # memory has poc_options with 3 items
    # user_message = "go with option 2"
    # Assert ToolResult.status == "parallel"
    # Assert len(parallel_tools) == 5
    # Assert tool names match expected set

async def test_confirmation_tool_names_correct():
    # Assert parallel_tools contains: generate_diagram, generate_bom, generate_jep,
    # generate_terraform, generate_presentation

async def test_no_poc_options_in_memory_returns_needs_input():
    # memory.decision_context has no poc_options
    # user_message = "go with option 1"
    # Assert ToolResult.status == "needs_input"

async def test_option_index_selection():
    # user_message = "option 2" → assert parallel_tools[0].args contains option index 1 data
    # user_message = "option 3" → assert option index 2

async def test_ambiguous_confirmation_defaults_to_recommendation():
    # user_message = "let's do it" → assert uses options[0]
```

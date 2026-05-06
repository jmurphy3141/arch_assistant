# Task p2e: Extract generate_diagram Tool Handler

## Goal

Create `agent/tools/diagram.py` with a `DiagramHandler` class that implements
the full `generate_diagram` pipeline as a ToolHandler. The diagram tool has
its own preflight check (`_diagram_has_sufficient_context`) and delegates the
actual generation to `_call_generate_diagram` in `archie_loop.py`.

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/bom.py
pytest tests/test_tools_bom.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/tools/diagram.py`
- `tests/test_tools_diagram.py`

**Do NOT touch:**

- `agent/archie_loop.py` — leave `_execute_tool` and `_call_generate_diagram` intact
- `agent/archie_memory.py`
- Any existing file

## What to implement

### `agent/tools/diagram.py`

The `generate_diagram` branch in `_execute_tool` does this (in order):
1. Calls `archie_memory._hydrate_tool_args_from_context(...)` and `_enforce_memory_contract_on_tool_args(...)`
2. Checks `archie_memory._diagram_has_sufficient_context(context, args, user_message)` — returns early if insufficient
3. Calls `_execute_tool_core("generate_diagram", ...)` which calls `_call_generate_diagram`
4. Returns the result

```python
class DiagramHandler:
    def __init__(
        self,
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
        text_runner: Any,
        a2a_base_url: str = "",
    ) -> None: ...

    async def __call__(
        self,
        args: dict[str, Any],
        *,
        memory: MemorySnapshot | None,
        context: dict[str, Any],
        trace_id: str,
    ) -> ToolResult: ...
```

Inside `__call__`:

**Step 1 — Context grounding**
```python
import agent.archie_memory as archie_memory

ctx = context
args = archie_memory._hydrate_tool_args_from_context(
    tool_name="generate_diagram",
    args=args,
    context=ctx,
    decision_context=memory.decision_context if memory else {},
    user_message=args.get("_user_message", ""),  # injected by archie_wiring.py
)
args = archie_memory._enforce_memory_contract_on_tool_args(
    tool_name="generate_diagram",
    args=args,
    context=ctx,
)
```

**Step 2 — Sufficiency check**
```python
user_message = args.get("_user_message", "")
if ctx and not archie_memory._diagram_has_sufficient_context(
    context=ctx,
    args=args,
    user_message=user_message,
):
    return ToolResult(
        summary="Please upload or paste BOM/resource details first, or describe the workload/components you want in the diagram.",
        status="needs_input",
        clarification="Please upload or paste BOM/resource details first, or describe the workload/components you want in the diagram.",
    )
```

**Step 3 — Sub-agent call (import from archie_loop)**

The diagram generation logic lives in `archie_loop._call_generate_diagram`. Import it:
```python
from agent.archie_loop import _call_generate_diagram
```

```python
try:
    summary, artifact_key, result_data = await _call_generate_diagram(
        args=args,
        customer_id=self._customer_id,
        a2a_base_url=self._a2a_base_url,
    )
except Exception as exc:
    return ToolResult(
        summary=f"Diagram generation failed: {exc}",
        status="blocked",
    )
```

**Step 4 — Build result**
```python
# needs_clarification surfaces as needs_input
if result_data.get("diagram_recovery_status") == "needs_clarification":
    clarify = summary
    return ToolResult(
        summary=clarify,
        status="needs_input",
        clarification=clarify,
    )
return ToolResult(
    summary=summary,
    status="ok",
    artifact_key=artifact_key,
    data=result_data,
)
```

## Test: `tests/test_tools_diagram.py`

Use monkeypatch to stub `_call_generate_diagram` and `archie_memory` helpers.
Stub `_hydrate_tool_args_from_context` and `_enforce_memory_contract_on_tool_args`
to return args unchanged. Stub `_diagram_has_sufficient_context` to return `True`
by default.

1. `test_diagram_ok`
   Stub `_call_generate_diagram` to return `("Diagram generated. Key: diagrams/foo.drawio", "diagrams/foo.drawio", {})`.
   Assert `result.status == "ok"` and `result.artifact_key == "diagrams/foo.drawio"`.

2. `test_diagram_insufficient_context`
   Stub `_diagram_has_sufficient_context` to return `False`.
   Assert `result.status == "needs_input"` and `_call_generate_diagram` was NOT called.

3. `test_diagram_needs_clarification`
   Stub `_call_generate_diagram` to return
   `("Clarify components.", "", {"diagram_recovery_status": "needs_clarification"})`.
   Assert `result.status == "needs_input"` and `result.clarification == "Clarify components."`.

4. `test_diagram_sub_agent_error`
   Stub `_call_generate_diagram` to raise `Exception("connection refused")`.
   Assert `result.status == "blocked"`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/tools/diagram.py` exits 0
2. `pytest tests/test_tools_diagram.py -v` — 4 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "archie_loop\._execute_tool" agent/tools/diagram.py` — no matches

## Do NOT Do

- Do not duplicate `_call_generate_diagram` — import and call it as-is
- Do not modify `agent/archie_loop.py`
- Do not rewrite diagram generation logic

## Commit Message

```
p2e: extract generate_diagram tool handler
```

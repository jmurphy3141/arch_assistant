# Task p2f: Extract generate_terraform Tool Handler

## Goal

Create `agent/tools/terraform.py` with a `TerraformHandler` class that implements
the full `generate_terraform` pipeline as a ToolHandler. The terraform tool has
a scope-mediation preflight and calls the terraform sub-agent via
`sub_agent_client.call_sub_agent("terraform", ...)`.

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/diagram.py
pytest tests/test_tools_diagram.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/tools/terraform.py`
- `tests/test_tools_terraform.py`

**Do NOT touch:**

- `agent/archie_loop.py` — leave `_execute_tool` intact
- `agent/archie_memory.py`
- Any existing file

## What to implement

### `agent/tools/terraform.py`

The `generate_terraform` branch in `_execute_tool` does this (in order):
1. Calls `archie_memory._hydrate_tool_args_from_context(...)` and `_enforce_memory_contract_on_tool_args(...)`
2. If `archie_memory._has_architecture_definition(context)` is True and
   `archie_memory._terraform_scope_is_bounded(...)` returns False — calls
   `archie_memory._mediate_specialist_questions(...)` to gather scope inputs
3. Calls `sub_agent_client.call_sub_agent("terraform", ...)` with the hydrated args
4. Parses result: saves terraform bundle via `document_store.save_terraform_bundle`
5. Returns ToolResult

```python
class TerraformHandler:
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
decision_context = memory.decision_context if memory else {}
user_message = args.get("_user_message", "")  # injected by archie_wiring.py
args = archie_memory._hydrate_tool_args_from_context(
    tool_name="generate_terraform",
    args=args,
    context=ctx,
    decision_context=decision_context,
    user_message=user_message,
)
args = archie_memory._enforce_memory_contract_on_tool_args(
    tool_name="generate_terraform",
    args=args,
    context=ctx,
)
```

**Step 2 — Scope mediation**
```python
if (
    ctx
    and archie_memory._has_architecture_definition(ctx)
    and not archie_memory._terraform_scope_is_bounded(
        context=ctx,
        args=args,
        decision_context=decision_context,
        user_message=user_message,
    )
):
    return ToolResult(
        summary="Terraform scope clarification required.",
        status="needs_input",
        clarification="Please clarify which modules or resources to include in the Terraform bundle.",
    )
```

**Step 3 — Sub-agent call**
```python
import agent.sub_agent_client as sub_agent_client
from agent.sub_agent_client import SubAgentError

raw_prompt = str(args.get("prompt", "") or "")
task = raw_prompt or str(args.get("_user_request_text", "") or "Generate Terraform for the current architecture.")

try:
    response = await sub_agent_client.call_sub_agent(
        "terraform",
        task=task,
        engagement_context={
            "customer_id": self._customer_id,
            "customer_name": self._customer_name,
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        },
        trace_id=trace_id,
    )
except SubAgentError as exc:
    return ToolResult(summary=f"Terraform sub-agent error: {exc}", status="blocked")
```

**Step 4 — Parse response**
```python
import agent.document_store as document_store
import asyncio

if str(response.get("status") or "").lower() == "needs_input":
    return ToolResult(
        summary=str(response.get("result") or "Terraform needs more input."),
        status="needs_input",
        clarification=str(response.get("result") or ""),
    )

from agent.archie_loop import _parse_terraform_sub_agent_result
files = _parse_terraform_sub_agent_result(response.get("result"))
saved = await asyncio.to_thread(
    document_store.save_terraform_bundle,
    self._store,
    self._customer_id,
    files,
    {"trace": response.get("trace", {}), "source": "sub_agent_client"},
)
key = str((saved.get("files") or {}).get("main.tf") or saved.get("latest_key") or "")
return ToolResult(
    summary=f"Terraform bundle v{saved.get('version')} saved.",
    status="ok",
    artifact_key=key,
    data={
        "terraform_files": files,
        "terraform_bundle": saved,
    },
)
```

## Test: `tests/test_tools_terraform.py`

Use monkeypatch to stub `sub_agent_client.call_sub_agent`, `archie_memory` helpers,
`document_store.save_terraform_bundle`, and `_parse_terraform_sub_agent_result`.

Stub `_hydrate_tool_args_from_context`, `_enforce_memory_contract_on_tool_args` to
return args unchanged. Stub `_has_architecture_definition` to return `True` and
`_terraform_scope_is_bounded` to return `True` by default.

1. `test_terraform_ok`
   Stub sub_agent to return `{"status": "ok", "result": "resource 'oci_core_vcn' 'main' {}"}`.
   Stub `_parse_terraform_sub_agent_result` to return `{"main.tf": "..."}`.
   Stub `document_store.save_terraform_bundle` to return `{"version": 1, "latest_key": "tf/main.tf"}`.
   Assert `result.status == "ok"` and `result.artifact_key == "tf/main.tf"`.

2. `test_terraform_needs_input`
   Stub sub_agent to return `{"status": "needs_input", "result": "Please define VCN CIDR."}`.
   Assert `result.status == "needs_input"` and `result.clarification == "Please define VCN CIDR."`.

3. `test_terraform_scope_not_bounded`
   Stub `_has_architecture_definition` to return `True` and
   `_terraform_scope_is_bounded` to return `False`.
   Assert `result.status == "needs_input"` and sub_agent was NOT called.

4. `test_terraform_sub_agent_error`
   Stub sub_agent to raise `SubAgentError("connection refused")`.
   Assert `result.status == "blocked"`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/tools/terraform.py` exits 0
2. `pytest tests/test_tools_terraform.py -v` — 4 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "archie_loop\._execute_tool" agent/tools/terraform.py` — no matches

## Do NOT Do

- Do not rewrite `_parse_terraform_sub_agent_result` — import it from archie_loop
- Do not modify `agent/archie_loop.py`
- Do not add safety rules inline — those belong in safety_rules.py

## Commit Message

```
p2f: extract generate_terraform tool handler
```

# Task p2g: Extract POV, JEP, and WAF Tool Handlers

## Goal

Create `agent/tools/specialists.py` with three handler classes: `PovHandler`,
`JepHandler`, and `WafHandler`. These three tools share the same sub-agent call
pattern. JEP adds a lifecycle lock check via `jep_lifecycle.generate_policy_block_payload`.
POV adds a sufficiency check via `archie_memory._pov_has_sufficient_context`.

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/terraform.py
pytest tests/test_tools_terraform.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/tools/specialists.py`
- `tests/test_tools_specialists.py`

**Do NOT touch:**

- `agent/archie_loop.py` — leave `_execute_tool` intact
- `agent/archie_memory.py`
- `agent/jep_lifecycle.py`
- Any existing file

## What to implement

### `agent/tools/specialists.py`

All three handlers share the same class skeleton:

```python
class _SpecialistHandler:
    """Base pattern for sub-agent specialist tools (pov, jep, waf)."""

    def __init__(
        self,
        agent_name: str,           # "pov", "jep", or "waf"
        doc_type: str,             # "pov", "jep", or "waf"
        store: ObjectStoreBase,
        customer_id: str,
        customer_name: str,
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

Then:
```python
class PovHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("pov", "pov", store, customer_id, customer_name)

class JepHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("jep", "jep", store, customer_id, customer_name)

class WafHandler(_SpecialistHandler):
    def __init__(self, store, customer_id, customer_name):
        super().__init__("waf", "waf", store, customer_id, customer_name)
```

#### `_SpecialistHandler.__call__` pipeline

**Step 1 — JEP lifecycle lock (JEP only)**
```python
if self._agent_name == "jep":
    import agent.jep_lifecycle as jep_lifecycle
    policy_block = await asyncio.to_thread(
        jep_lifecycle.generate_policy_block_payload,
        self._store,
        self._customer_id,
    )
    if policy_block is not None:
        return ToolResult(
            summary="JEP generation is locked because an approved JEP exists. Request revision first.",
            status="blocked",
            data={
                "jep_state": policy_block.get("jep_state", {}),
                "reason_codes": list(policy_block.get("reason_codes", [])),
                "required_next_step": policy_block.get("required_next_step", ""),
                "lock_outcome": "blocked",
            },
        )
```

**Step 2 — Context grounding**
```python
import agent.archie_memory as archie_memory

ctx = context
decision_context = memory.decision_context if memory else {}
user_message = args.get("_user_message", "")  # injected by archie_wiring.py
args = archie_memory._hydrate_tool_args_from_context(
    tool_name=f"generate_{self._agent_name}",
    args=args,
    context=ctx,
    decision_context=decision_context,
    user_message=user_message,
)
args = archie_memory._enforce_memory_contract_on_tool_args(
    tool_name=f"generate_{self._agent_name}",
    args=args,
    context=ctx,
)
```

**Step 3 — POV sufficiency check (POV only)**
```python
if self._agent_name == "pov" and ctx and not archie_memory._pov_has_sufficient_context(
    context=ctx,
    decision_context=decision_context,
    args=args,
    user_message=user_message,
):
    return ToolResult(
        summary="POV clarification required before Archie drafts the customer narrative.",
        status="needs_input",
        clarification="POV clarification required before Archie drafts the customer narrative.",
        data={"questions": archie_memory._pov_targeted_questions()},
    )
```

**Step 4 — Sub-agent call**
```python
import agent.sub_agent_client as sub_agent_client
from agent.sub_agent_client import SubAgentError

feedback = str(args.get("feedback", "") or "")
raw_request = feedback or f"Generate {'a customer POV' if self._agent_name == 'pov' else 'the ' + self._agent_name.upper()} from current engagement context."

try:
    response = await sub_agent_client.call_sub_agent(
        self._agent_name,
        task=raw_request,
        engagement_context={
            "customer_id": self._customer_id,
            "customer_name": self._customer_name,
            "feedback": feedback,
            "architect_brief": dict(args.get("_architect_brief", {}) or {}),
        },
        trace_id=trace_id,
    )
except SubAgentError as exc:
    return ToolResult(summary=f"{self._agent_name.upper()} sub-agent error: {exc}", status="blocked")
```

**Step 5 — Parse and persist**
```python
import agent.document_store as document_store

if str(response.get("status") or "").lower() == "needs_input":
    return ToolResult(
        summary=str(response.get("result") or f"{self._agent_name.upper()} needs more input."),
        status="needs_input",
        clarification=str(response.get("result") or ""),
    )

saved = await asyncio.to_thread(
    document_store.save_doc,
    self._store,
    self._doc_type,
    self._customer_id,
    str(response.get("result") or ""),
    {"trace": response.get("trace", {}), "source": "sub_agent_client"},
)
key = str(saved.get("key", "") or "")

# JEP: update lifecycle state after successful generation
if self._agent_name == "jep":
    import agent.jep_lifecycle as jep_lifecycle
    jep_state = await asyncio.to_thread(jep_lifecycle.mark_generated, self._store, self._customer_id)
    response.update({"jep_state": jep_state, "lock_outcome": "allowed"})

return ToolResult(
    summary=f"{self._agent_name.upper()} v{saved.get('version')} saved.",
    status="ok",
    artifact_key=key,
    data=response,
)
```

## Test: `tests/test_tools_specialists.py`

Use monkeypatch to stub `sub_agent_client.call_sub_agent`, `archie_memory` helpers,
`document_store.save_doc`, and `jep_lifecycle` functions.

Stub `_hydrate_tool_args_from_context` and `_enforce_memory_contract_on_tool_args`
to return args unchanged. Stub `_pov_has_sufficient_context` to return `True` by
default.

1. `test_pov_ok`
   Stub sub_agent to return `{"status": "ok", "result": "POV document text."}`.
   Stub `document_store.save_doc` to return `{"key": "docs/pov_v1.md", "version": 1}`.
   Assert `result.status == "ok"` and `result.artifact_key == "docs/pov_v1.md"`.

2. `test_pov_insufficient_context`
   Stub `_pov_has_sufficient_context` to return `False`.
   Assert `result.status == "needs_input"` and sub_agent was NOT called.

3. `test_jep_ok`
   Stub `jep_lifecycle.generate_policy_block_payload` to return `None` (not locked).
   Stub sub_agent to return `{"status": "ok", "result": "JEP document text."}`.
   Stub `document_store.save_doc` to return `{"key": "docs/jep_v1.md", "version": 1}`.
   Stub `jep_lifecycle.mark_generated` to return `{"jep_state": "generated"}`.
   Assert `result.status == "ok"` and `result.data["lock_outcome"] == "allowed"`.

4. `test_jep_locked`
   Stub `jep_lifecycle.generate_policy_block_payload` to return
   `{"jep_state": {"state": "approved"}, "reason_codes": ["already_approved"], "required_next_step": "Request revision."}`.
   Assert `result.status == "blocked"` and sub_agent was NOT called.

5. `test_waf_ok`
   Stub sub_agent to return `{"status": "ok", "result": "WAF review text."}`.
   Stub `document_store.save_doc` to return `{"key": "docs/waf_v1.md", "version": 1}`.
   Assert `result.status == "ok"` and `result.artifact_key == "docs/waf_v1.md"`.

6. `test_waf_needs_input`
   Stub sub_agent to return `{"status": "needs_input", "result": "Please provide architecture context."}`.
   Assert `result.status == "needs_input"` and `result.clarification == "Please provide architecture context."`.

7. `test_specialist_sub_agent_error`
   Stub sub_agent to raise `SubAgentError("timeout")` for WafHandler.
   Assert `result.status == "blocked"`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/tools/specialists.py` exits 0
2. `pytest tests/test_tools_specialists.py -v` — 7 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "archie_loop\._execute_tool" agent/tools/specialists.py` — no matches

## Do NOT Do

- Do not duplicate `jep_lifecycle` logic — call the existing functions
- Do not modify `agent/archie_loop.py`
- Do not add a fourth class for an unused tool

## Commit Message

```
p2g: extract generate_pov, generate_jep, generate_waf tool handlers
```

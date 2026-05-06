# Task p2b: ArchieMemory — Memory Protocol Implementation

## Goal

Create `agent/archie_memory_impl.py` with an `ArchieMemory` class that
implements the `skillforge.protocols.Memory` protocol. This is the bridge
between Forge's domain-agnostic memory contract and Archie's OCI-specific
context store.

## Prerequisite Check

```bash
python3.11 -c "from skillforge.protocols import Memory; print('ok')"
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -5
```

If either fails, stop and report.

## Scope

**Only create this file:**

- `agent/archie_memory_impl.py`

**Do NOT touch:**

- `agent/archie_memory.py`
- `agent/archie_loop.py`
- `agent/context_store.py`
- Any existing file

## What to implement

```python
# agent/archie_memory_impl.py

class ArchieMemory:
    """
    Implements skillforge.protocols.Memory for the Archie OCI context store.

    Bridges Forge's generic MemorySnapshot contract to the existing
    context_store / archie_memory infrastructure without modifying either.
    """

    def __init__(self, store: ObjectStoreBase) -> None:
        self._store = store

    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot:
        """
        Build a MemorySnapshot from the Archie context store blob.

        Populate fields as follows:
          session_id     = session_id
          facts          = archie state facts_summary + infrastructure profile
                           Use context_store.get_archie_state(context) to get
                           the archie sub-dict, then extract:
                             - facts_summary (str)
                             - latest_approved_constraints (dict)
                             - resolved_questions (list)
                           Build a plain dict: {"facts_summary": ...,
                           "constraints": ..., "resolved_questions": ...}
          constraints    = dict(archie_state.get("latest_approved_constraints") or {})
          prior_artifacts = {
              "generate_diagram": diagram_key if present in context agents dict,
              "generate_bom":     bom xlsx key if present,
              ... (other artifact keys present in context)
          }
          decision_context = context.get("latest_decision_context") or {}
          raw            = context  (full blob for tools needing direct access)
        """
        ...

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Return context unchanged. Actual persistence (record_tool_decision_state,
        persist_tool_metadata) is handled inside each tool handler. This method
        exists to satisfy the Memory protocol; Archie's tool handlers manage
        their own persistence directly.
        """
        return context
```

### How to read prior_artifacts

Check these paths in context (using safe .get() chains):
- Diagram key: `context.get("agents", {}).get("diagram", {}).get("diagram_key", "")`
- BOM xlsx: `context.get("agents", {}).get("bom", {}).get("xlsx_key", "")`
  or `context.get("agents", {}).get("bom", {}).get("artifact_key", "")`
- POV: `context.get("agents", {}).get("pov", {}).get("artifact_key", "")`
- JEP: `context.get("agents", {}).get("jep", {}).get("artifact_key", "")`
- WAF: `context.get("agents", {}).get("waf", {}).get("artifact_key", "")`
- Terraform: `context.get("agents", {}).get("terraform", {}).get("artifact_key", "")`

Only include keys where the value is a non-empty string.

## Test to write alongside

Create `tests/test_archie_memory_impl.py` with these tests:

1. `test_assemble_empty_context`
   Pass `context={}`. Assert `MemorySnapshot(session_id="s1")` is returned
   with empty facts, constraints, prior_artifacts, decision_context.

2. `test_assemble_with_facts`
   Pass a context dict with:
   ```python
   {"archie": {"facts_summary": "3-tier web app", "latest_approved_constraints": {"region": "us-chicago-1"}}}
   ```
   Assert `snapshot.facts["facts_summary"] == "3-tier web app"` and
   `snapshot.constraints["region"] == "us-chicago-1"`.

3. `test_assemble_prior_artifacts`
   Pass context with `{"agents": {"diagram": {"diagram_key": "diagrams/foo.drawio"}}}`.
   Assert `snapshot.prior_artifacts["generate_diagram"] == "diagrams/foo.drawio"`.

4. `test_update_returns_context_unchanged`
   Call `update(session_id="s", tool_name="generate_bom", result=ToolResult(...), context={"x": 1})`.
   Assert return value equals `{"x": 1}`.

5. `test_implements_memory_protocol`
   ```python
   from skillforge.protocols import Memory
   assert isinstance(ArchieMemory(store=None), Memory)
   ```

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_memory_impl.py` exits 0
2. `pytest tests/test_archie_memory_impl.py -v` — 5 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "from agent.archie_memory import\|import agent.archie_loop" agent/archie_memory_impl.py`
   returns nothing — no circular imports

## Do NOT Do

- Do not modify `agent/archie_memory.py`
- Do not modify `agent/context_store.py`
- Do not add persistence calls to `update()` — return context unchanged
- Do not touch any existing file

## Commit Message

```
p2b: add ArchieMemory implementing skillforge Memory protocol
```

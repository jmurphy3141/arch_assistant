# Task p2h: Archie Wiring — Build Forge and Register All OCI Tools

## Goal

Create `agent/archie_wiring.py` with a `build_forge()` factory that wires every
OCI tool handler into a `skillforge.Forge` instance. This is the single place
where tool names are bound to handlers.

**Do not touch `agent/archie_loop.py` in this task.**
Wiring is a new module; integration into archie_loop is a separate follow-on task.

## Prerequisite Check

```bash
python3.11 -m compileall agent/tools/specialists.py
pytest tests/test_tools_specialists.py -v --tb=short 2>&1 | tail -3
```

If either fails, stop and report.

## Scope

**Only create these files:**

- `agent/archie_wiring.py`
- `tests/test_archie_wiring.py`

**Do NOT touch:**

- `agent/archie_loop.py`
- Any existing file

## What to implement

### `agent/archie_wiring.py`

```python
"""
archie_wiring.py — wire every OCI tool handler into a Forge instance.

Call build_forge() once at startup to get a fully configured Forge.
Inject the resulting instance into run_turn() when cutting over from
archie_loop._execute_tool.
"""
from __future__ import annotations

from typing import Any, Callable

from skillforge import Forge
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import ObjectStoreBase
import agent.hat_engine as hat_engine

from agent.tools.notes import NotesHandlers
from agent.tools.bom import BomHandler
from agent.tools.diagram import DiagramHandler
from agent.tools.terraform import TerraformHandler
from agent.tools.specialists import PovHandler, JepHandler, WafHandler


def build_forge(
    store: ObjectStoreBase,
    customer_id: str,
    customer_name: str,
    text_runner: Callable,
    a2a_base_url: str = "",
    base_system_prompt: str = "",
) -> Forge:
    """
    Instantiate and return a Forge wired with all OCI tool handlers.

    Parameters
    ----------
    store           : OCI Object Storage adapter (or in-memory stub for tests)
    customer_id     : Archie customer/engagement ID
    customer_name   : Human-readable customer name (for context hydration)
    text_runner     : async (prompt, system_msg, label) -> str  (LLM call)
    a2a_base_url    : Base URL for A2A sub-agent calls
    base_system_prompt : Archie orchestrator system prompt
    """
    memory = ArchieMemory(store=store)

    forge = Forge(
        base_system_prompt=base_system_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        max_iterations=5,
    )

    # --- In-process handlers ---
    notes = NotesHandlers(store=store, customer_id=customer_id, customer_name=customer_name)
    forge.register_tool("save_notes", notes.save_notes, memory_contract=True)
    forge.register_tool("get_summary", notes.get_summary)
    forge.register_tool("get_document", notes.get_document)

    # --- Sub-agent handlers ---
    forge.register_tool(
        "generate_bom",
        BomHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_diagram",
        DiagramHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_terraform",
        TerraformHandler(
            store=store,
            customer_id=customer_id,
            customer_name=customer_name,
            text_runner=text_runner,
            a2a_base_url=a2a_base_url,
        ),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_pov",
        PovHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_jep",
        JepHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
    )
    forge.register_tool(
        "generate_waf",
        WafHandler(store=store, customer_id=customer_id, customer_name=customer_name),
        memory_contract=True,
    )

    return forge
```

## Test: `tests/test_archie_wiring.py`

Use in-memory stubs. Import `build_forge` and verify:

1. `test_build_forge_returns_forge`
   Call `build_forge(store=None, customer_id="c1", customer_name="Acme",
   text_runner=dummy_runner)`.
   Assert the return value is an instance of `skillforge.Forge`.

2. `test_all_tools_registered`
   Call `build_forge(...)`.
   Check that all 9 tool names are registered:
   `save_notes`, `get_summary`, `get_document`,
   `generate_bom`, `generate_diagram`, `generate_terraform`,
   `generate_pov`, `generate_jep`, `generate_waf`.
   Use `forge._registry.get(name)` (or the public accessor) to verify each is not None.

3. `test_memory_contract_tools`
   Assert that `generate_bom`, `generate_diagram`, `generate_terraform`,
   `generate_pov`, `generate_jep`, `generate_waf` all have `memory_contract=True`.
   (Use `forge._registry.requires_memory(name)`.)

4. `test_no_archie_loop_import`
   ```python
   import importlib, sys
   # archie_loop must NOT be imported as a side-effect of importing archie_wiring
   # (it would trigger OCI auth checks at import time)
   for mod in list(sys.modules.keys()):
       if "archie_loop" in mod:
           del sys.modules[mod]
   import agent.archie_wiring  # should succeed without importing archie_loop
   assert "agent.archie_loop" not in sys.modules
   ```

   Note: if `DiagramHandler` imports `_call_generate_diagram` from `archie_loop` at
   module level, this test will fail. Fix: use a lazy import inside `__call__`.

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_wiring.py` exits 0
2. `pytest tests/test_archie_wiring.py -v` — 4 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "from agent.archie_loop import\|import agent.archie_loop" agent/archie_wiring.py`
   — no matches (lazy imports only, inside handler `__call__` methods)

## Do NOT Do

- Do not modify `agent/archie_loop.py`
- Do not call `build_forge()` at module level in archie_wiring.py (caller does that)
- Do not hardcode customer_id or customer_name defaults in build_forge

## Note on DiagramHandler lazy import

`DiagramHandler.__call__` imports `_call_generate_diagram` from `archie_loop`
at call time (not at module import time):

```python
async def __call__(self, args, *, memory, context, trace_id):
    from agent.archie_loop import _call_generate_diagram  # lazy — avoids circular import
    ...
```

This keeps `archie_wiring.py` import-clean. If the import is at module level in
`diagram.py`, move it inside `__call__` before completing this task.

## Commit Message

```
p2h: add archie_wiring.build_forge() — register all OCI tool handlers with Forge
```

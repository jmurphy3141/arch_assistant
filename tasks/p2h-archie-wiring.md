# Task p2h: Archie Wiring — Build Forge and Register All OCI Tools

## Goal

Create `agent/archie_wiring.py` with:
1. `build_forge()` — instantiates a `skillforge.Forge` wired with all 9 OCI tool handlers
2. `ArchiePromptEnricher` — injects the per-round memory context (decision context, 
   facts summary) into the prompt before each LLM call, keeping this OCI-specific
   enrichment out of Forge core

This is the single place where OCI tool names are bound to handlers.
**Do not modify `agent/archie_loop.py` in this task.**

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

Call build_forge() once per customer session to get a fully configured Forge.
archie_loop.py imports build_forge() for the p2i cutover task.
"""
from __future__ import annotations

from typing import Any, Callable

from skillforge import Forge
from skillforge.types import MemorySnapshot
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import ObjectStoreBase
import agent.hat_engine as hat_engine

from agent.tools.notes import NotesHandlers
from agent.tools.bom import BomHandler
from agent.tools.diagram import DiagramHandler
from agent.tools.terraform import TerraformHandler
from agent.tools.specialists import PovHandler, JepHandler, WafHandler


class ArchiePromptEnricher:
    """
    Injects per-round OCI context into the prompt before each LLM call.

    Forge calls this before every ReAct round. This keeps OCI-specific
    prompt assembly out of Forge core.

    Injects:
      - decision_context summary (constraints, approved region, sizing)
      - facts_summary (accumulated SA-provided facts)
    """

    def __call__(self, prompt: str, memory: MemorySnapshot) -> str:
        parts: list[str] = []

        facts_summary = str((memory.facts or {}).get("facts_summary") or "").strip()
        if facts_summary:
            parts.append(f"[Archie Facts]\n{facts_summary}\n[/Archie Facts]")

        if memory.constraints:
            import json
            constraints_text = json.dumps(memory.constraints, ensure_ascii=False)
            parts.append(f"[Archie Constraints]\n{constraints_text}\n[/Archie Constraints]")

        if not parts:
            return prompt
        return "\n\n".join(parts) + "\n\n" + prompt


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
    store              : OCI Object Storage adapter (or in-memory stub for tests)
    customer_id        : Archie customer/engagement ID
    customer_name      : Human-readable customer name (for context hydration)
    text_runner        : async (prompt, system_msg, label) -> str  (LLM call)
    a2a_base_url       : Base URL for A2A sub-agent calls
    base_system_prompt : Archie orchestrator system prompt
    """
    memory = ArchieMemory(store=store)
    enricher = ArchiePromptEnricher()

    forge = Forge(
        base_system_prompt=base_system_prompt,
        hat_engine=hat_engine,
        memory=memory,
        text_runner=text_runner,
        prompt_enricher=enricher,
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

### Key constraint: no top-level `archie_loop` import

`DiagramHandler.__call__` imports `_call_generate_diagram` from `archie_loop` at
call time (not at module level). If it imports at module level, `test_no_archie_loop_import`
below will fail. Verify and fix if needed before completing this task.

## Test: `tests/test_archie_wiring.py`

1. `test_build_forge_returns_forge`
   Call `build_forge(store=None, customer_id="c1", customer_name="Acme", text_runner=dummy_runner)`.
   Assert the return value is an instance of `skillforge.Forge`.

2. `test_all_tools_registered`
   Call `build_forge(...)`.
   Assert all 9 tool names are registered:
   `save_notes`, `get_summary`, `get_document`,
   `generate_bom`, `generate_diagram`, `generate_terraform`,
   `generate_pov`, `generate_jep`, `generate_waf`.
   Use `forge._registry.get(name) is not None` for each.

3. `test_memory_contract_tools`
   Assert that `generate_bom`, `generate_diagram`, `generate_terraform`,
   `generate_pov`, `generate_jep`, `generate_waf` all have `memory_contract=True`.
   Use `forge._registry.requires_memory(name)`.

4. `test_prompt_enricher_injects_facts`
   Instantiate `ArchiePromptEnricher()`.
   Create a `MemorySnapshot(session_id="s1", facts={"facts_summary": "3-tier app"})`.
   Call `enricher("USER: hello", snapshot)`.
   Assert `"[Archie Facts]"` in result and `"3-tier app"` in result.

5. `test_prompt_enricher_empty_memory`
   Create a `MemorySnapshot(session_id="s1")` (no facts, no constraints).
   Call `enricher("USER: hello", snapshot)`.
   Assert result == `"USER: hello"` (no injection when memory is empty).

6. `test_no_archie_loop_import`
   ```python
   import sys
   # Purge any previously loaded archie_loop from sys.modules
   for mod in list(sys.modules):
       if "archie_loop" in mod:
           del sys.modules[mod]
   # Re-import archie_wiring — should succeed without pulling in archie_loop
   import importlib
   import agent.archie_wiring
   importlib.reload(agent.archie_wiring)
   assert "agent.archie_loop" not in sys.modules, (
       "archie_wiring imported archie_loop at module level — move to lazy import inside __call__"
   )
   ```

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_wiring.py` exits 0
2. `pytest tests/test_archie_wiring.py -v` — 6 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
4. `grep "^from agent.archie_loop\|^import agent.archie_loop" agent/archie_wiring.py`
   — no matches (all archie_loop references must be lazy imports inside `__call__`)

## Do NOT Do

- Do not modify `agent/archie_loop.py`
- Do not call `build_forge()` at module level in `archie_wiring.py`
- Do not hardcode `customer_id` or `customer_name` defaults in `build_forge`
- Do not add per-tool `skill_guidance` here — that belongs in each tool's handler or a skill file

## Commit Message

```
p2h: add archie_wiring.build_forge() and ArchiePromptEnricher
```

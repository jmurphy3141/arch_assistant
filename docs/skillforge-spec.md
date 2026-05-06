# SkillForge — Framework Spec

## What it is

SkillForge is the domain-agnostic orchestrator core extracted from Archie.
It owns the ReAct loop, hat dispatch, and tool dispatch. It knows nothing
about OCI, diagrams, BOMs, or Terraform. Domain knowledge lives in:

- Registered `ToolHandler` callables (your OCI tools)
- Hat markdown files (`agent/hats/*.md`)
- Your `Memory` implementation (`agent/archie_memory.py`)

## Package layout

```
skillforge/
  __init__.py       # public API: Forge, ToolResult, TurnResult, MemorySnapshot
  types.py          # MemorySnapshot, ToolResult, ToolCall, TurnResult (no deps)
  protocols.py      # ToolHandler, Memory, SafetyChecker (typing.Protocol)
  registry.py       # ToolRegistry — register_tool(), tool_contract_block()
  forge.py          # Forge — run_turn(), the ReAct loop
```

## Framework / application boundary

```
skillforge/          ← FRAMEWORK (no OCI knowledge)
  Forge              orchestrator ReAct loop
  ToolRegistry       tool registration and system-prompt assembly
  MemorySnapshot     data contract between orchestrator and tools
  protocols          ToolHandler, Memory, SafetyChecker interfaces

agent/               ← APPLICATION (OCI-specific)
  archie_loop.py     thin: wire Forge, register OCI tools, call forge.run_turn()
  archie_memory.py   implements Memory protocol (OCI-specific enrichment)
  tools/             one file per OCI tool handler
    bom.py           async def handle(args, *, memory, context, trace_id) -> ToolResult
    diagram.py
    terraform.py
    pov.py / jep.py / waf.py
  safety_rules.py    implements SafetyChecker protocol
  hats/*.md          hat content (OCI-specific lens, framework-agnostic mechanism)
```

## Core contract: ToolHandler

```python
async def my_tool_handler(
    args: dict,
    *,
    memory: MemorySnapshot | None,   # None if memory_contract=False
    context: dict,                   # raw context store blob
    trace_id: str,
) -> ToolResult:
    ...
    return ToolResult(
        summary="BOM generated with 12 line items",
        status="ok",                 # "ok" | "needs_input" | "blocked"
        data={"bom_payload": ...},   # raw data for critic/safety review
        artifact_key="bom/...",      # object-store key if artifact was produced
    )
```

Rules:
- Must be `async`.
- Must not raise — surface failures as `ToolResult(status="blocked")`.
- Must not mutate `memory` or `context`.
- If `memory_contract=True`, the `memory` snapshot contains all accumulated
  facts and must be treated as authoritative (facts over defaults).

## Core contract: Memory

```python
class MyMemory:
    def assemble(self, *, session_id, context, user_message) -> MemorySnapshot:
        # Build the snapshot from context store — called once per turn before loop
        ...

    def update(self, *, session_id, tool_name, result, context) -> dict:
        # Refresh context after a tool call — return updated context blob
        ...
```

`MemorySnapshot` fields:
- `session_id` — str
- `facts` — accumulated customer facts (region, sizing, constraints, etc.)
- `constraints` — hard constraints from decision context
- `prior_artifacts` — `{tool_name: artifact_key}` for latest known artifacts
- `decision_context` — structured decision context dict
- `raw` — full context store blob for tools that need direct access

## Wiring pattern (in archie_loop.py after migration)

```python
from skillforge import Forge
from agent.archie_memory import ArchieMemory
from agent import tools
from agent.safety_rules import check as safety_check

forge = Forge(
    base_system_prompt=ARCHIE_SYSTEM_PROMPT,
    hat_engine=hat_engine,
    memory=ArchieMemory(),
    store=object_store,
    text_runner=llm_call,
)

forge.register_tool("generate_bom",
    tools.bom.handle,
    memory_contract=True,
    description='{"prompt": "<workload sizing / BOM request>"}',
    safety_checker=safety_check,
)
forge.register_tool("generate_diagram",
    tools.diagram.handle,
    memory_contract=True,
    description='{"bom_text": "<optional inline BOM/context>"}',
)
forge.register_tool("generate_terraform",
    tools.terraform.handle,
    memory_contract=True,
    description='{"prompt": "<optional module/constraints text>"}',
    safety_checker=safety_check,
)
forge.register_tool("save_notes",
    tools.notes.handle,
    memory_contract=False,
    description='{"text": "<notes text>"}',
)
# ... etc

result = await forge.run_turn(
    session_id=customer_id,
    user_message=user_message,
    context=context_blob,
    history=conversation_history,
)
```

## Migration phases

### Phase 1 (now — complete)
`skillforge/` package created with full interface stubs.
No production code changed. `archie_loop.py` still owns the loop.

### Phase 2 (next)
Create `agent/tools/` with one file per OCI tool. Each file contains a single
`async def handle(args, *, memory, context, trace_id) -> ToolResult` function
extracted from `archie_loop._execute_tool()`. No behavior change — just moving
code to the right home.

### Phase 3 (after phase 2 stable)
Refactor `archie_loop.py` to instantiate `Forge`, register tools, and delegate
`run_turn()` to `forge.run_turn()`. The OCI system prompt stays in `agent/`.
`archie_memory.py` implements `Memory` protocol. `safety_rules.check` becomes
the `SafetyChecker`.

### Phase 4 (SkillForge v1 stable)
Move `skillforge/` to its own package. At this point `agent/` is purely the
OCI application. A different domain (AWS, Azure, GCP) can use SkillForge by
implementing `Memory` and registering domain-specific tool handlers.

## What does NOT move to skillforge/

- OCI tool implementations (BOM, diagram, Terraform, etc.)
- `archie_memory.py` BOM enrichment and deictic detection
- `decision_context.py` OCI-specific fact extraction
- `safety_rules.py` OCI-specific rule implementations
- Hat content in `agent/hats/*.md` (the mechanism is framework; the content is OCI)
- `bom_service.py`, `drawio_generator.py`, etc.
- Sub-agent servers in `sub_agents/`

## Design principles

1. **ToolHandler is the only seam.** Forge never calls `bom_service` or
   `drawio_generator` directly. All domain work goes through registered handlers.

2. **Memory is a hard contract.** Every `memory_contract=True` handler receives
   the same `MemorySnapshot`. Handlers must not ask for facts already in `memory.facts`.

3. **Safety is deterministic.** `SafetyChecker` must not call an LLM.
   LLM-based review lives in hats (critic, governor), not safety checkers.

4. **Hats are content, not code.** Adding a new hat is dropping a `.md` file.
   The framework loads and injects it; no code change needed.

5. **Forge has no OCI imports.** If `import oci` appears in `skillforge/`, it's
   a boundary violation.

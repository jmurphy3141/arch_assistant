# SkillForge — Interface Specification

## What it is

SkillForge is a domain-agnostic polymath agent orchestration framework. It owns
the ReAct loop, hat dispatch, and tool dispatch. It has zero domain knowledge.
Domain knowledge lives in registered `ToolHandler` callables, hat markdown files,
and a `Memory` implementation.

**Boundary rule:** if `import oci` (or `import boto3`, or any domain SDK) appears
inside `skillforge/`, it is a boundary violation.

---

## Package layout

```
skillforge/
  __init__.py      public API surface
  types.py         MemorySnapshot, ToolResult, ToolCall, TurnResult, ToolStatus
  protocols.py     ToolHandler, Memory, SafetyChecker, HatEngine, PromptEnricher
  registry.py      ToolRegistry — register_tool(), tool_contract_block()
  forge.py         Forge — run_turn(), the ReAct loop
```

---

## Core interfaces

### `Forge.__init__`

```python
Forge(
    *,
    base_system_prompt: str,          # domain-specific system prompt
    hat_engine: HatEngine,            # agent/hat_engine.py (structurally typed)
    memory: Memory,                   # domain Memory implementation
    text_runner: AsyncTextRunner,     # async (prompt, system, label) -> str
    prompt_enricher: PromptEnricher | None = None,  # per-round context injection
    max_iterations: int = 5,
    history_window: int = 20,         # prior turns included in prompt
)
```

### `Forge.register_tool`

```python
forge.register_tool(
    name: str,                        # exact string LLM emits in {"tool": "name"}
    handler: ToolHandler,             # async callable — see ToolHandler below
    *,
    description: str = "",            # args schema shown in system prompt
    args_schema: dict[str, str] | None = None,  # alternative to description
    memory_contract: bool = False,    # pass MemorySnapshot to handler
    safety_checker: SafetyChecker | None = None,  # deterministic, no LLM
    skill_guidance: str = "",         # markdown prepended to task arg
    parallel_safe: bool = False,      # may run concurrently with peers
    retry_on_needs_input: bool = False,  # retry once on needs_input before surfacing
)
```

### `Forge.run_turn`

```python
result: TurnResult = await forge.run_turn(
    session_id: str,
    user_message: str,
    context: dict[str, Any],          # context store blob; Forge never mutates it
    history: list[dict] | None = None,
)
```

---

## ToolHandler protocol

```python
async def my_handler(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,    # None when memory_contract=False
    context: dict[str, Any],          # raw context store blob (read-only)
    trace_id: str,
) -> ToolResult:
    ...
```

**Rules:**
- Must be async.
- Must not raise — surface failures as `ToolResult(status="blocked")`.
- Must not mutate `memory` or `context` (`MemorySnapshot` is frozen).
- When input is insufficient: `ToolResult(status="needs_input", clarification="...")`.
- `memory` is the authoritative source of facts — do not ask for what is already there.

---

## Memory protocol

```python
class MyMemory:
    def assemble(
        self,
        *,
        session_id: str,
        context: dict[str, Any],
        user_message: str,
    ) -> MemorySnapshot:
        # Build snapshot from context store — called once per turn,
        # and again after each memory_contract tool call.
        ...

    def update(
        self,
        *,
        session_id: str,
        tool_name: str,
        result: ToolResult,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Incorporate tool result into context; return updated blob.
        # Do not mutate context in place — return a new dict.
        ...
```

---

## MemorySnapshot fields

| Field | Type | Purpose |
|---|---|---|
| `session_id` | `str` | stable session identifier |
| `facts` | `dict` | accumulated domain facts (region, sizing, constraints, etc.) |
| `constraints` | `dict` | hard constraints from decision context |
| `prior_artifacts` | `dict[str, str]` | `{tool_name: artifact_key}` for latest artifacts |
| `decision_context` | `dict` | structured decision context for this turn |
| `raw` | `dict` | full context store blob for tools needing direct access |

`MemorySnapshot` is **frozen** — handlers cannot mutate it.

---

## ToolResult fields

| Field | Type | Purpose |
|---|---|---|
| `summary` | `str` | one sentence outcome, always populated |
| `status` | `"ok" \| "needs_input" \| "blocked"` | drives Forge behaviour |
| `data` | `dict` | raw payload for safety/critic review |
| `artifact_key` | `str` | object-store key for produced artifact |
| `clarification` | `str` | message surfaced to user when `needs_input` |

---

## ReAct loop behaviour by status

| status | Forge action |
|---|---|
| `ok` | record artifact, refresh memory, append result, continue loop |
| `needs_input` | surface `clarification` as reply; or retry once if `retry_on_needs_input=True` |
| `blocked` | remove artifact if recorded, append block reason, continue loop |

---

## HatEngine protocol

```python
class HatEngine(Protocol):
    def get_hat_tool_definitions(self) -> list[dict]: ...
    def apply_hat(self, active_hats: list[str], hat_name: str) -> list[str]: ...
    def drop_hat(self, active_hats: list[str], hat_name: str) -> list[str]: ...
    def warn_stale_hats(self, active_hats, rounds, max_rounds=5) -> list[str]: ...
    def inject_hats(self, prompt: str, active_hats: list[str]) -> str: ...
```

`agent/hat_engine.py` already satisfies this protocol. A different hat engine
(e.g. loading from a database instead of the filesystem) can be substituted.

---

## PromptEnricher hook

```python
class MyEnricher:
    def __call__(self, prompt: str, memory: MemorySnapshot) -> str:
        summary = build_memory_summary(memory.facts)
        dc = summarize_decision_context(memory.decision_context)
        return f"{prompt}\n\n[Session Context]\n{summary}\n{dc}\n[End Session Context]"
```

Keeps domain-specific prompt assembly (memory summary, decision context,
conversation summary) out of `forge.py`. Pass as `prompt_enricher=MyEnricher()`
at Forge init.

---

## Minimal non-OCI example (AWS)

```python
from skillforge import Forge, ToolResult, MemorySnapshot
from skillforge.protocols import AsyncTextRunner
import agent.hat_engine as hat_engine   # mechanism is domain-agnostic

# 1. Tool handlers

async def ec2_sizing_handler(args, *, memory, context, trace_id):
    region = (memory.facts.get("region") if memory else None) or "us-east-1"
    # ... call AWS Pricing API ...
    return ToolResult(
        summary=f"EC2 sizing complete for {region}.",
        status="ok",
        data={"instances": [...]},
    )

async def cloudformation_handler(args, *, memory, context, trace_id):
    if not (memory and memory.facts.get("architecture_defined")):
        return ToolResult(
            summary="Architecture not yet defined.",
            status="needs_input",
            clarification="Please define the target architecture before generating CloudFormation.",
        )
    return ToolResult(
        summary="CloudFormation template generated.",
        status="ok",
        artifact_key="cf/template.yaml",
    )

# 2. Memory (minimal no-op)

class AWSMemory:
    def assemble(self, *, session_id, context, user_message) -> MemorySnapshot:
        return MemorySnapshot(
            session_id=session_id,
            facts=context.get("facts", {}),
            constraints=context.get("constraints", {}),
        )
    def update(self, *, session_id, tool_name, result, context) -> dict:
        return context   # no-op; real impl would persist result.data

# 3. Wire up

async def my_llm_call(prompt: str, system: str, label: str) -> str:
    # ... call Bedrock, OpenAI, etc.
    ...

forge = Forge(
    base_system_prompt="You are an AWS solutions architect assistant...",
    hat_engine=hat_engine,
    memory=AWSMemory(),
    text_runner=my_llm_call,
)
forge.register_tool("size_ec2",
    ec2_sizing_handler,
    memory_contract=True,
    description='{"workload": "<description of workload to size>"}',
)
forge.register_tool("generate_cloudformation",
    cloudformation_handler,
    memory_contract=True,
    description='{"modules": "<optional list of modules to include>"}',
)

result = await forge.run_turn(
    session_id="aws-eng-001",
    user_message="Size a 3-tier web app for 10k concurrent users in us-west-2",
    context={},
)
```

Zero OCI code. Same `Forge`, same `hat_engine`, different tools and `Memory`.

---

## Migration phases (Archie → SkillForge)

### Phase 1 — Skeleton (this PR)
`skillforge/` package present with full interfaces and working Forge.
No production code changed. `archie_loop.py` still owns its own loop.

### Phase 2 — Extract tool handlers
Create `agent/tools/` with one file per OCI tool. Each exports
`async def handle(args, *, memory, context, trace_id) -> ToolResult`.
Extraction order (safest first): `save_notes` → `generate_bom` →
`generate_diagram` → `generate_terraform` → `pov/jep/waf`.
Run `pytest tests/test_specialist_mode_routing.py -v` after each file.

### Phase 3 — Wire Forge into archie_loop
Create `agent/archie_wiring.py` that instantiates `Forge` and registers all
OCI tool handlers. Shadow-run both `archie_loop.run_turn()` and
`forge.run_turn()` in parallel with `SKILLFORGE_SHADOW=1` for validation.

### Phase 4 — Cut over
Replace `archie_loop.run_turn()` body with a ~30-line wrapper around
`forge.run_turn()`. Delete the inline `_execute_tool()` dispatcher.

### Phase 5 — Extract SkillForge
Move `skillforge/` to its own package. `agent/` is now purely the OCI
application layer.

---

## What stays in agent/ (never moves to skillforge/)

- OCI tool implementations (`agent/tools/`)
- `archie_memory.py` — OCI-specific memory enrichment (BOM grounding, etc.)
- `decision_context.py` — OCI fact extraction
- `safety_rules.py` — OCI-specific safety rule implementations
- Hat content in `agent/hats/*.md` (mechanism is framework; content is OCI)
- All sub-agent servers in `sub_agents/`
- `bom_service.py`, `drawio_generator.py`, etc.

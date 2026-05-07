# SkillForge Phase 2 — Implementation Plan

## 1. Phase 2 Objectives

**Success looks like:** `skillforge.Forge` is the production orchestrator for Archie.
`archie_loop.py` becomes a ~50-line adapter that wires OCI tools into Forge and
delegates `run_turn()` to it. Every OCI tool handler lives in `agent/tools/`.
The existing OCI architect capability is 100% preserved with zero test regressions.

A developer targeting a new domain (AWS, GCP, Kubernetes) can instantiate Forge
by implementing two things: a `Memory` class and a set of `ToolHandler` callables.
They touch zero files in `skillforge/` and zero files in `agent/`.

---

## 2. Core Interfaces (Final Spec)

```python
# Forge.__init__
Forge(
    *,
    base_system_prompt: str,
    hat_engine: HatEngine,            # structurally typed — agent/hat_engine.py satisfies this
    memory: Memory,                   # domain Memory implementation
    text_runner: AsyncTextRunner,     # async (prompt, system_msg, label) -> str
    prompt_enricher: PromptEnricher | None = None,
    max_iterations: int = 5,
    history_window: int = 20,
)

# Forge.register_tool
forge.register_tool(
    name: str,                        # exact string LLM emits in {"tool": "name"}
    handler: ToolHandler,             # async (args, *, memory, context, trace_id) -> ToolResult
    *,
    description: str = "",
    memory_contract: bool = False,    # pass MemorySnapshot to handler
    safety_checker: SafetyChecker | None = None,
    skill_guidance: str = "",         # markdown prepended to task arg
    parallel_safe: bool = False,
    retry_on_needs_input: bool = False,
)

# Forge.run_turn
result: TurnResult = await forge.run_turn(
    session_id=str,
    user_message=str,
    context=dict,        # full context store blob; Forge never mutates it directly
    history=list | None,
)

# ToolHandler protocol
async def my_handler(
    args: dict[str, Any],
    *,
    memory: MemorySnapshot | None,   # None when memory_contract=False; frozen
    context: dict[str, Any],         # raw context store blob, read-only
    trace_id: str,
) -> ToolResult: ...

# Memory protocol
class Memory(Protocol):
    def assemble(self, *, session_id, context, user_message) -> MemorySnapshot: ...
    def update(self, *, session_id, tool_name, result, context) -> dict: ...
```

---

## 3. What _execute_tool Actually Does (Critical Understanding)

Before extracting, we must understand the current pipeline. Each `_execute_tool`
call does six things in order:

```
1. PREFLIGHT    — per-tool context checks (BOM grounding, diagram sufficiency,
                  JEP lifecycle lock, POV/Terraform scope mediation)
2. SKILL-INJECT — _inject_skill_into_tool_args(): enriches args with guidance
3. CORE-CALL    — _execute_tool_core(): calls the sub-agent or in-process handler
4. MEDIATION    — _mediate_specialist_questions(): handles follow-up Q&A
5. CRITIC       — _critic_refine_if_needed(): critic hat review + retry
6. PERSIST      — safety check, record state, persist metadata, build trace
```

Steps 2, 4, 5, 6 are SHARED across all tools. Steps 1 and 3 are tool-specific.

**Extraction target:** Each `agent/tools/*.py` handler implements the full pipeline
(steps 1-6) for its tool, using the shared helpers already in `archie_memory.py`
and `archie_loop.py`. The handler signature matches `ToolHandler` but internally
calls the same helpers the old `_execute_tool` called.

This means: we are NOT rewriting the OCI logic. We are re-organizing it.

---

## 4. File-by-File Implementation Plan

### NEW: `skillforge/forge.py` (already exists — no changes in Phase 2)
Phase 2 does not touch skillforge/. The framework is stable.

### NEW: `agent/tools/__init__.py`
Empty. Marks the package.

### NEW: `agent/tools/shared.py`
Shared pipeline helpers used by multiple tool handlers.
Extracted (not copied) from `archie_loop.py`:
- `run_preflight(tool_name, args, ...) -> _SkillDecision | None`
- `run_skill_injection(tool_name, args, ...) -> dict`
- `run_critic_refine(tool_name, args, result, ...) -> tuple`
- `run_expert_review(tool_name, args, result, ...) -> tuple`
- `run_safety_and_persist(tool_name, result, store, ...) -> tuple`
These are the six pipeline stages as callable functions.

### NEW: `agent/tools/notes.py`
Handles: `save_notes`, `get_summary`, `get_document`
These are in-process (no sub-agent). Simplest extraction.

```python
async def save_notes_handle(args, *, memory, context, trace_id) -> ToolResult: ...
async def get_summary_handle(args, *, memory, context, trace_id) -> ToolResult: ...
async def get_document_handle(args, *, memory, context, trace_id) -> ToolResult: ...
```

### NEW: `agent/tools/bom.py`
Handles: `generate_bom`
BOM-specific: calls `archie_memory._prepare_bom_tool_args()` in preflight,
then shared pipeline, then `sub_agent_client.call_sub_agent("bom", ...)`.

### NEW: `agent/tools/diagram.py`
Handles: `generate_diagram`
Diagram-specific: checks `_diagram_has_sufficient_context()`, then shared pipeline,
then `sub_agent_client.call_sub_agent("diagram", ...)`.

### NEW: `agent/tools/terraform.py`
Handles: `generate_terraform`
Terraform-specific: checks `_terraform_scope_is_bounded()`, mediates if needed,
then shared pipeline, then `sub_agent_client.call_sub_agent("terraform", ...)`.
Has safety checker for hardcoded OCIDs.

### NEW: `agent/tools/pov.py`, `agent/tools/jep.py`, `agent/tools/waf.py`
Each handles one specialist sub-agent.
JEP has lifecycle lock check (`jep_lifecycle.generate_policy_block_payload()`).
POV has `_pov_has_sufficient_context()` mediation.

### NEW: `agent/archie_memory_impl.py`
Implements the `Memory` protocol for Archie:
```python
class ArchieMemory:
    def __init__(self, store: ObjectStoreBase): ...
    def assemble(self, *, session_id, context, user_message) -> MemorySnapshot: ...
    def update(self, *, session_id, tool_name, result, context) -> dict: ...
```
`assemble()` calls `context_store.read_context()` + `archie_memory` helpers
to build `MemorySnapshot.facts`, `.constraints`, `.prior_artifacts`, `.decision_context`.
`update()` calls `_record_tool_decision_state()` + `_persist_tool_metadata()`.

### NEW: `agent/archie_wiring.py`
Instantiates Forge and registers all OCI tools.
```python
def build_forge(store, text_runner, a2a_base_url) -> Forge: ...
```
This is the single place where OCI tool names are bound to handlers.
`archie_loop.py` imports and calls `build_forge()`.

### MODIFY: `agent/archie_loop.py`
Final state after Phase 2:
- `run_turn()` delegates to `forge.run_turn()` after loading history and context
- `_execute_tool()` is DELETED (replaced by per-tool handlers)
- `_execute_tool_core()` is DELETED (inlined into tool handlers)
- `build_forge()` call added at module level (lazy init)
Target: ~100 lines (down from 6,455)

---

## 5. Migration Strategy

**Rule: archie_loop._execute_tool() stays intact until every tool is extracted.**
Each task extracts ONE tool. After each extraction:
1. The new handler in `agent/tools/` is registered with Forge
2. The old branch in `_execute_tool()` is replaced with a call to the new handler
3. Full test suite must pass before the next extraction

### Shadow mode (Tasks 2E, optional)
```python
# In archie_loop.run_turn(), with env var:
if os.getenv("SKILLFORGE_SHADOW"):
    asyncio.create_task(_shadow_forge_run(forge, user_message, context))
```
Run both paths in parallel to compare outputs before cutting over.

### Cutover (Task 2H)
Once all tools are extracted and shadow-tested, replace `archie_loop.run_turn()`
body with:
```python
async def run_turn(**kwargs) -> dict:
    context = await asyncio.to_thread(context_store.read_context, store, ...)
    history = document_store.load_conversation_history(store, customer_id)
    result = await _forge.run_turn(
        session_id=kwargs["customer_id"],
        user_message=kwargs["user_message"],
        context=context,
        history=history,
    )
    document_store.save_conversation_turns(store, customer_id, [...])
    return {"reply": result.reply, "tool_calls": ..., "artifacts": result.artifacts, ...}
```

---

## 6. Test Plan

### `tests/test_forge.py` (Task 2A — first task)
Unit tests for Forge with fully mocked dependencies. No OCI imports.
- `test_plain_reply`: LLM returns text, no tool call → TurnResult with reply
- `test_tool_call_ok`: LLM returns tool call → handler called → TurnResult with artifact
- `test_needs_input_surfaces`: handler returns needs_input → reply = clarification
- `test_needs_input_retry`: retry_on_needs_input=True → loop retries once
- `test_blocked_removes_artifact`: handler returns blocked → artifact not in result
- `test_safety_check_blocks`: safety_checker returns False → artifact removed, reply blocked
- `test_hat_activation`: use_hat_X → active_hats updated, inject_hats called
- `test_skill_guidance_injected`: skill_guidance prepended to task arg before handler
- `test_memory_refreshed_after_tool`: memory.update() called after memory_contract tool
- `test_unknown_tool_continues`: unknown tool name → logged, loop continues
- `test_system_prompt_cached`: _get_system_msg() returns same object on repeat calls
- `test_max_iterations`: loop terminates at max_iterations

### `tests/test_forge_aws.py` (Task 2A)
Non-OCI smoke test proving domain-agnostic reuse.
Mocks: AWSMemory, two tool handlers, hat_engine, text_runner.
Asserts: TurnResult has expected reply and artifacts with zero OCI imports.

### `tests/test_archie_tools_*.py` (Tasks 2C–2G)
One file per tool handler. Tests the handler in isolation with mocked
sub_agent_client and archie_memory helpers.

### Regression: `tests/test_specialist_mode_routing.py` (all tasks)
Run after every extraction. Must stay green throughout.

---

## 7. Execution Order (Codex Tasks)

| Task | File | Prereq |
|---|---|---|
| **2A** | `tests/test_forge.py`, `tests/test_forge_aws.py` | PR #65 merged |
| **2B** | `agent/archie_memory_impl.py` | 2A merged |
| **2C** | `agent/tools/__init__.py`, `agent/tools/notes.py` | 2B merged |
| **2D** | `agent/tools/bom.py` | 2C merged |
| **2E** | `agent/tools/diagram.py` | 2D merged |
| **2F** | `agent/tools/terraform.py` | 2E merged |
| **2G** | `agent/tools/pov.py`, `agent/tools/jep.py`, `agent/tools/waf.py` | 2F merged |
| **2H** | `agent/archie_wiring.py` + cutover in `archie_loop.py` | 2G merged |

Each task is one PR. Each PR must pass `pytest tests/ -v -m "not live"` before merge.

---

## 8. Risks & Rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| `_execute_tool` has hidden side effects not visible in the branch | High | Read the full function before each extraction; copy helpers verbatim |
| `archie_memory._prepare_bom_tool_args()` has complex grounding logic | High | Don't rewrite — call it unchanged from the new handler |
| Parallel tool path in archie_loop not replicated in Forge | Medium | Keep parallel path in archie_loop until Task 2H; Forge adds parallel after cutover |
| `_mediate_specialist_questions()` has circular import back to archie_loop | Medium | Already uses late import; handler replicates the same pattern |
| Conversation history save/load is woven into archie_loop | Medium | archie_loop.run_turn() keeps history management until Task 2H cutover |

**Rollback:** Every task keeps `archie_loop._execute_tool()` intact until Task 2H.
Any task can be reverted without breaking production — the old path is always available.
The `SKILLFORGE_SHADOW` env var allows parallel validation before final cutover.

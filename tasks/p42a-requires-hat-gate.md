# Task p42a: `requires_hat` Gate — Forge Auto-Activates Expert Hats

## Objective

Forge's expert pre-action and post-review are gated on expert hats being
active. Hat activation is currently driven entirely by the manager LLM: it
must call `use_hat_<name>` before calling a domain tool. If it skips that
step, every expert reasoning hook silently no-ops.

Add `requires_hat: str | None` to tool registration. Before dispatching any
domain tool, Forge checks the required hat against `active_hats`. If the hat
is missing, Forge activates it in code (deterministic — no LLM call). The
expert pre-action and post-review then fire as a natural consequence because
the hat is now guaranteed active.

This is a code gate, not a prompt suggestion. The LLM cannot bypass it.

Also add section-header validation to `_run_step3_planning()` so that the
pre-loop planning call is structurally enforced the same way pre-action is
(p40a pattern).

---

## Scope

**Touch:**
- `skillforge/registry.py` — add `requires_hat` to `ToolSpec` and `ToolRegistry.register()`
- `skillforge/forge.py` — add `requires_hat` to `Forge.register_tool()`, add
  auto-activation block in `run_turn()` domain dispatch, add `_STEP3_PLANNING_HEADERS`
  constant and validation in `_run_step3_planning()`
- `agent/archie_wiring.py` — add `requires_hat=` to all six domain tool registrations

**Do NOT touch:** hat markdown files, test files, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/registry.py skillforge/forge.py agent/archie_wiring.py
grep "requires_hat\|hat_auto_activated\|_STEP3_PLANNING_HEADERS" skillforge/registry.py skillforge/forge.py agent/archie_wiring.py
# must be zero matches
```

---

## Changes

### 1. `skillforge/registry.py` — Add `requires_hat` to `ToolSpec`

Add `requires_hat: str | None = None` as the last field of the `ToolSpec` dataclass:

```python
@dataclass
class ToolSpec:
    name: str
    handler: ToolHandler
    description: str = ""
    args_schema: dict = field(default_factory=dict)
    memory_contract: bool = False
    safety_checker: SafetyChecker | None = None
    skill_guidance: str = ""
    parallel_safe: bool = False
    retry_on_needs_input: bool = False
    critique_enabled: bool = False
    requires_hat: str | None = None   # ← new
```

Add `requires_hat: str | None = None` parameter to `ToolRegistry.register()` and
pass it through to `ToolSpec(...)`:

```python
def register(
    self,
    name: str,
    handler: ToolHandler,
    *,
    description: str = "",
    args_schema: dict[str, str] | None = None,
    memory_contract: bool = False,
    safety_checker: SafetyChecker | None = None,
    skill_guidance: str = "",
    parallel_safe: bool = False,
    retry_on_needs_input: bool = False,
    critique_enabled: bool = False,
    requires_hat: str | None = None,   # ← new
) -> None:
    ...
    spec = ToolSpec(
        ...
        requires_hat=requires_hat,
    )
```

---

### 2. `skillforge/forge.py` — Three sub-changes

#### 2a. Add `requires_hat` to `Forge.register_tool()`

Add `requires_hat: str | None = None` to the method signature and pass it
through to `self._registry.register(...)`:

```python
def register_tool(
    self,
    name: str,
    handler: ...,
    description: str = "",
    *,
    args_schema: dict[str, str] | None = None,
    memory_contract: bool = False,
    safety_checker: ... = None,
    skill_guidance: str = "",
    parallel_safe: bool = False,
    retry_on_needs_input: bool = False,
    critique_enabled: bool = False,
    requires_hat: str | None = None,   # ← new
) -> None:
    self._registry.register(
        name,
        handler,
        ...
        requires_hat=requires_hat,
    )
```

#### 2b. Auto-activation block in `run_turn()` domain dispatch

In the domain tool dispatch section of `run_turn()`, **immediately after the
`spec = self._registry.get(tool_name)` lookup and the unknown-tool check,
and BEFORE the existing `_run_expert_pre_action` call**, add:

```python
            # ── Hat auto-activation (requires_hat gate) ───────────────────────
            if spec.requires_hat and spec.requires_hat not in active_hats:
                try:
                    active_hats = self._hat_engine.apply_hat(active_hats, spec.requires_hat)
                    logger.info(
                        "[FORGE] Auto-activated required hat '%s' for tool '%s' session=%s",
                        spec.requires_hat, tool_name, session_id,
                    )
                    events.append(
                        TurnEvent(
                            type="hat_auto_activated",
                            message=(
                                f"Hat '{spec.requires_hat}' auto-activated "
                                f"(required by tool '{tool_name}')"
                            ),
                            data={"hat": spec.requires_hat, "tool": tool_name},
                        )
                    )
                except ValueError:
                    logger.warning(
                        "[FORGE] Tool '%s' requires unknown hat '%s' — proceeding without",
                        tool_name, spec.requires_hat,
                    )
```

The `_run_expert_pre_action` call that follows will now see the hat in
`active_hats` and fire correctly.

#### 2c. `_STEP3_PLANNING_HEADERS` constant + validation in `_run_step3_planning()`

Add the constant near the other `_EXPERT_*` module-level constants:

```python
_STEP3_PLANNING_HEADERS = (
    "STEP 1 — UNDERSTAND:",
    "STEP 2 — MEMORY ASSESSMENT:",
    "STEP 3 — PLAN + HAT SELECTION:",
)
```

In `_run_step3_planning()`, after `planning_text = raw.strip()` and before the
`logger.info` + event append, add header validation (same pattern as p40a):

```python
    # Section-header guard: all 3 planning sections must be present.
    missing_planning = [h for h in _STEP3_PLANNING_HEADERS if h not in planning_text]
    if missing_planning:
        logger.warning(
            "[STEP3_PLANNING] Missing sections %s session=%s — retrying",
            missing_planning, session_id,
        )
        planning_retry_prompt = (
            f"{planning_prompt}\n\n"
            f"[Your response is missing required sections: {', '.join(missing_planning)}. "
            "You MUST include all three labeled sections exactly as shown: "
            "STEP 1 — UNDERSTAND:, STEP 2 — MEMORY ASSESSMENT:, "
            "STEP 3 — PLAN + HAT SELECTION:. Rewrite with all three sections present.]"
        )
        try:
            raw = await self._text_runner(planning_retry_prompt, system_msg, "step3_planning_header_retry")
            planning_text = raw.strip()
        except Exception:
            logger.exception("[STEP3_PLANNING] Header retry failed session=%s", session_id)
        still_missing = [h for h in _STEP3_PLANNING_HEADERS if h not in planning_text]
        if still_missing:
            logger.warning(
                "[STEP3_PLANNING] Still missing sections %s after retry session=%s",
                still_missing, session_id,
            )
```

---

### 3. `agent/archie_wiring.py` — Add `requires_hat` to domain tool registrations

Verify hat filenames in `agent/hats/` before applying (run `ls agent/hats/`).
Expected mapping (hat name = filename without `.md`):

```python
forge.register_tool(
    "generate_bom",
    BomHandler(...),
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_bom_expert",
)
forge.register_tool(
    "generate_diagram",
    DiagramHandler(...),
    memory_contract=True,
    critique_enabled=True,
    requires_hat="diagram_for_oci",
)
forge.register_tool(
    "generate_terraform",
    TerraformHandler(...),
    memory_contract=True,
    critique_enabled=True,
    requires_hat="terraform_for_oci",
)
forge.register_tool(
    "generate_pov",
    PovHandler(...),
    memory_contract=True,
    requires_hat="oci_customer_pov_writer",
)
forge.register_tool(
    "generate_jep",
    JepHandler(...),
    memory_contract=True,
    requires_hat="jep_writer",
)
forge.register_tool(
    "generate_waf",
    WafHandler(...),
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_waf_reviewer",
)
```

Notes tools (save_notes, get_summary, get_document) need no hat — leave unchanged.

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/registry.py skillforge/forge.py agent/archie_wiring.py
   ```

2. `requires_hat` wired across all three files:
   ```bash
   grep "requires_hat" skillforge/registry.py skillforge/forge.py agent/archie_wiring.py | wc -l
   # must be ≥ 10
   ```

3. Auto-activation event and log marker present:
   ```bash
   grep "hat_auto_activated\|Auto-activated required hat" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

4. Planning header validation present:
   ```bash
   grep "_STEP3_PLANNING_HEADERS\|step3_planning_header_retry" skillforge/forge.py | wc -l
   # must be ≥ 2
   ```

5. No-hat smoke test (existing pattern — hat engine's apply_hat must accept the name):
   ```bash
   python3.11 -c "
   import asyncio
   from skillforge.forge import Forge
   from skillforge.types import MemorySnapshot, ToolResult

   class NullMemory:
       def assemble(self, *, session_id, context, user_message):
           return MemorySnapshot(raw={}, formatted='')
       def update(self, *, session_id, tool_name, result, context):
           return context

   class NullHatEngine:
       def load_hats(self): return {}
       def apply_hat(self, hats, name): return hats + [name]
       def drop_hat(self, hats, name): return [h for h in hats if h != name]
       def warn_stale_hats(self, hats, rounds): return []
       def inject_hats(self, prompt, hats): return prompt
       def get_hat_tool_definitions(self): return []
       def build_expert_block(self, name): return ''
       def build_memory_view_block(self, name, snap): return ''
       def get_transition_suggestions(self, hats, msg): return []
       def get_suggested_next_hat(self, name): return None
       def get_coordination_rules(self, name): return {}
       def get_hat_meta(self, name): return {}
       def get_parallel_hats(self, name): return []
       def get_handoff_message(self, name): return None

   async def null_runner(prompt, system_msg, role):
       return 'plain reply'

   forge = Forge(
       base_system_prompt='You are an assistant.',
       hat_engine=NullHatEngine(),
       memory=NullMemory(),
       text_runner=null_runner,
   )
   result = asyncio.run(forge.run_turn(
       session_id='test', user_message='hello', context={}
   ))
   assert result.reply == 'plain reply'
   print('no-hat path OK')
   "
   ```

6. Auto-activation smoke test:
   ```bash
   python3.11 -c "
   import asyncio, json
   from skillforge.forge import Forge
   from skillforge.types import MemorySnapshot, ToolResult

   class NullMemory:
       def assemble(self, *, session_id, context, user_message):
           return MemorySnapshot(raw={}, formatted='')
       def update(self, *, session_id, tool_name, result, context):
           return context

   class NullHatEngine:
       def load_hats(self): return {}
       def apply_hat(self, hats, name): return hats + [name]
       def drop_hat(self, hats, name): return [h for h in hats if h != name]
       def warn_stale_hats(self, hats, rounds): return []
       def inject_hats(self, prompt, hats): return prompt
       def get_hat_tool_definitions(self): return []
       def build_expert_block(self, name): return ''
       def build_memory_view_block(self, name, snap): return ''
       def get_transition_suggestions(self, hats, msg): return []
       def get_suggested_next_hat(self, name): return None
       def get_coordination_rules(self, name): return {}
       def get_hat_meta(self, name): return {}
       def get_parallel_hats(self, name): return []
       def get_handoff_message(self, name): return None

   call_count = 0
   async def tool_runner(prompt, system_msg, role):
       global call_count
       call_count += 1
       if call_count == 1:
           return json.dumps({'tool': 'my_tool', 'args': {}})
       return 'done'

   async def my_handler(args, *, memory, context, trace_id):
       return ToolResult(summary='tool ran', status='ok')

   forge = Forge(
       base_system_prompt='You are an assistant.',
       hat_engine=NullHatEngine(),
       memory=NullMemory(),
       text_runner=tool_runner,
   )
   forge.register_tool('my_tool', my_handler, requires_hat='myhat')
   result = asyncio.run(forge.run_turn(
       session_id='test', user_message='run it', context={}
   ))
   auto_events = [e for e in result.events if e.type == 'hat_auto_activated']
   assert len(auto_events) >= 1, f'Expected hat_auto_activated event, got: {[e.type for e in result.events]}'
   assert auto_events[0].data['hat'] == 'myhat'
   assert auto_events[0].data['tool'] == 'my_tool'
   print('auto-activation OK:', auto_events[0].message)
   "
   ```

7. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p42a: requires_hat gate — Forge auto-activates expert hats before domain tools

Tools registered with requires_hat='<hat>' are now guaranteed to run with
that hat active. If the manager LLM skipped hat activation, Forge activates
it in code before dispatch — expert pre-action and post-review then fire as
a natural consequence. Also adds section-header validation to step3_planning
(same retry pattern as p40a).
```

Branch: `claude/p42a` (from main). Push when done.

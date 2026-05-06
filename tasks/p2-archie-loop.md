# Task: Extract archie_loop.py and thin orchestrator_agent.py
Phase: 2
Status: todo
Depends on: p2-archie-memory.md merged to main

## Goal
Extract `run_turn`, `_execute_tool`, `_execute_tool_core`, all intent-classification
predicates, prompt builders, and artifact reply helpers from
`agent/orchestrator_agent.py` into `agent/archie_loop.py`.
Reduce `agent/orchestrator_agent.py` to a ~30-line thin entry point that
re-exports `run_turn`.

No behaviour change. Tests must pass after the move.

---

## Prerequisite
`agent/archie_memory.py` must already exist (created in p2-archie-memory.md).
`orchestrator_agent.py` must already import `agent.archie_memory as archie_memory`
and call memory functions via `archie_memory.X(...)`.

---

## Files to create

### `agent/archie_loop.py`
One responsibility: the ReAct loop, tool dispatch, intent classification, and
reply construction. Imports memory functions from `agent.archie_memory`.

Move ALL of the following functions verbatim from `orchestrator_agent.py`:

**Core ReAct loop (current lines ~154–1531):**
- `run_turn` (the main async entry point)
- All nested helper closures defined inside `run_turn` (e.g. `_finalize_turn`,
  `_save_context_note_only`, `_run_generation_step`) — move them to module level
  if they are currently closures, or keep them nested if they only reference
  variables closed over by `run_turn`
- `_execute_tool`
- `_execute_tool_core`

**Architect brief and context summarisation (current line ~2629):**
- `_build_architect_brief`

**Tool architecture classification (current line ~4509):**
- `_is_architecture_tool`

**Tool context and diagram inference (current lines ~4738–5900):**
- `_infer_tool_context_source`
- `_diagram_artifact_view_from_result`
- `_infer_diagram_name_from_key`

**Decision and checkpoint state recording (current lines ~5929–6206):**
- `_record_shared_agent_state`
- `_record_tool_decision_state`
- `_record_approved_checkpoint_inputs`

**Diagram error classification (current lines ~6728–6733):**
- `_is_diagram_system_error`
- `_is_diagram_invariant_error`

**A2A artifact extraction (current line ~7196):**
- `_extract_a2a_artifact_data`

**Intent classification — artifact targeting (current lines ~8007–8444):**
- `_infer_turn_target_artifact`
- `_target_artifact_to_tool`
- `_is_explicit_artifact_download_request`
- `_is_explicit_artifact_verification_request`
- `_is_export_only_request`
- `_is_workbook_only_request`
- `_is_pure_download_or_link_request`
- `_is_existing_artifact_access_request`
- `_checkpoint_blocks_artifact_action_reply`
- `_artifact_downloads_from_context`
- `_build_artifact_link_reply`
- `_candidate_artifact_refs`
- `_build_artifact_verification_reply`

**Intent classification — conversation type (current lines ~8616–8926):**
- `_is_architecture_chat_only_request`
- `_build_architecture_chat_reply`
- `_is_change_update_intent`
- `_is_update_confirm_message`
- `_is_update_cancel_message`
- `_is_checkpoint_approve_message`
- `_is_checkpoint_reject_message`
- `_is_note_capture_only_request`
- `_is_recall_intent`
- `_is_migration_target_recall_intent`
- `_infer_superseded_decision_ids`

**Tool call parsing (last functions in the file, ~lines 8877–8926):**
- `_parse_tool_call`
- `_normalize_tool_payload`

**Important**: After moving the above, `orchestrator_agent.py` will still contain
many functions not listed here (prompt builders, BOM-specific helpers that were
not moved to archie_memory, etc.). Move ALL remaining non-constant, non-import
code to `archie_loop.py`. The goal is that `orchestrator_agent.py` becomes a
thin re-export file.

Specifically: whatever functions remain in `orchestrator_agent.py` after the
archie_memory extraction — move them all to `archie_loop.py` in this task,
unless they are:
- Module-level constants (e.g. `ORCHESTRATOR_SYSTEM_MSG`, `CPU_SKU_TO_MEM_SKU`,
  `_PENDING_UPDATE_WORKFLOWS`)
- The `TurnIntent` dataclass

Move those constants and the dataclass into `archie_loop.py` as well.

---

## Import pattern inside archie_loop.py

`archie_loop.py` must import memory functions via module reference, not
`from ... import`:

```python
import agent.archie_memory as archie_memory
```

All calls to memory functions inside archie_loop.py must use:
```python
archie_memory._pov_has_sufficient_context(...)
archie_memory._hydrate_tool_args_from_context(...)
# etc.
```

This ensures that `monkeypatch.setattr(archie_memory, "_pov_has_sufficient_context", fake)`
in tests reaches the actual call site.

Similarly, `archie_loop.py` imports `agent.sub_agent_client` as a module:
```python
import agent.sub_agent_client as sub_agent_client
```
And calls as `await sub_agent_client.call_sub_agent(...)`.

---

## Files to modify

### `agent/orchestrator_agent.py` (becomes the thin entry point)
Replace the entire file with approximately:
```python
"""
orchestrator_agent.py
---------------------
Thin entry point. All logic lives in agent/archie_loop.py.
This module re-exports run_turn for backward compatibility.
"""
from __future__ import annotations

import agent.archie_memory as archie_memory  # noqa: F401 — re-exported for test patching
from agent.archie_loop import (              # noqa: F401
    run_turn,
    _execute_tool,
    _execute_tool_core,
    TurnIntent,
    ORCHESTRATOR_SYSTEM_MSG,
    CPU_SKU_TO_MEM_SKU,
    _PENDING_UPDATE_WORKFLOWS,
)
# Re-export archie_memory names that existing tests monkeypatch via this module
_pov_has_sufficient_context      = archie_memory._pov_has_sufficient_context
_diagram_has_sufficient_context  = archie_memory._diagram_has_sufficient_context
_terraform_scope_is_bounded      = archie_memory._terraform_scope_is_bounded
_build_context_summary_for_skills = archie_memory._build_context_summary_for_skills
_hydrate_tool_args_from_context  = archie_memory._hydrate_tool_args_from_context
_enforce_memory_contract_on_tool_args = archie_memory._enforce_memory_contract_on_tool_args
_mediate_specialist_questions    = archie_memory._mediate_specialist_questions
_handle_pending_specialist_questions = archie_memory._handle_pending_specialist_questions
_bom_followup_should_hydrate_from_context = archie_memory._bom_followup_should_hydrate_from_context

# Keep a reference to critic_agent for tests that patch orchestrator_agent.critic_agent
import agent.critic_agent as critic_agent  # noqa: F401
```

Note: the re-exported names at the bottom of orchestrator_agent.py are ONLY for
backward compatibility with existing tests. They do NOT affect runtime behaviour
because all call sites in archie_loop.py call via `archie_memory.X(...)` (module
reference). These aliases can be deleted in Phase 4 once tests are updated.

### Test files that monkeypatch `orchestrator_agent._execute_tool_core` or
`orchestrator_agent._execute_tool`

Update those patches to target `archie_loop`:
```python
import agent.archie_loop as archie_loop
monkeypatch.setattr(archie_loop, "_execute_tool_core", fake)
monkeypatch.setattr(archie_loop, "_execute_tool", fake)
```

Run `grep -rn "monkeypatch.setattr(orchestrator_agent" tests/` and update each
site to use `archie_loop` for loop functions and `archie_memory` for memory
functions. For functions now aliased in the thin orchestrator_agent.py, patching
`orchestrator_agent.X` will still work (the alias is mutable), so you only MUST
update patches for `_execute_tool_core` and `_execute_tool` since run_turn calls
them directly by name within archie_loop's globals.

---

## Files to NOT touch
- Any file in `sub_agents/`
- `drawing_agent_server.py`
- `agent/archie_memory.py` (already correct from the previous task)
- Any agent in `agent/graphs/` (thin wrappers, leave for Phase 4)

---

## What to do
1. Create `agent/archie_loop.py`. Open with:
   ```python
   """
   archie_loop.py
   --------------
   Archie's ReAct loop: run_turn, tool dispatch, intent classification,
   and reply construction. Memory and context functions live in archie_memory.py.
   """
   from __future__ import annotations

   import agent.archie_memory as archie_memory
   import agent.sub_agent_client as sub_agent_client
   ```
   Then add all remaining imports that the moved code needs (copy from
   orchestrator_agent.py — only what is actually used).

2. Move all functions, constants, and the TurnIntent dataclass listed above into
   `archie_loop.py` verbatim. Replace all direct calls to memory functions with
   `archie_memory.X(...)`.

3. Replace `agent/orchestrator_agent.py` with the thin entry point shown above.

4. Run `python3.11 -m compileall agent/archie_loop.py agent/archie_memory.py agent/orchestrator_agent.py`
   and fix any import errors.

5. Run `grep -n "^def \|^async def \|^class " agent/orchestrator_agent.py`
   — must return only re-export aliases and `import` statements, no function bodies.

6. Update test monkeypatch sites for `_execute_tool_core` and `_execute_tool`.

7. Run `pytest tests/ -v -m "not live"`. Must be 9 or fewer failures (the
   pre-existing version-string failures). No new failures allowed.

---

## Acceptance criteria
- `python3.11 -m compileall agent/archie_loop.py` exits 0
- `python3.11 -m compileall agent/orchestrator_agent.py` exits 0
- `wc -l agent/orchestrator_agent.py` prints 50 or fewer lines
- `wc -l agent/archie_loop.py` prints fewer lines than the original `orchestrator_agent.py` had before Phase 2
- `grep -c "^def \|^async def " agent/orchestrator_agent.py` prints 0
- `pytest tests/ -v -m "not live"` — no new failures beyond the 9 pre-existing ones
- `grep -n "from agent.pov_agent\|from agent.jep_agent\|from agent.waf_agent\|from agent.bom_service import" agent/archie_loop.py` returns nothing

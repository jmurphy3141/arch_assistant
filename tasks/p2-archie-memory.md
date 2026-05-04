# Task: Extract archie_memory.py
Phase: 2
Status: todo
Depends on: all Phase 1 tasks merged to main

## Goal
Extract all memory, context, assessment, and BOM-helper functions from
`agent/orchestrator_agent.py` into a new `agent/archie_memory.py` module.
No behaviour change. Tests must pass after the move.

---

## Why this split is safe
These functions are "leaf" helpers — they do not call `run_turn`, `_execute_tool`,
or `_execute_tool_core`. They are called by those functions, not the other way
around. Moving them out reduces `orchestrator_agent.py` by approximately
4,000 lines without touching the ReAct loop.

---

## Files to create

### `agent/archie_memory.py`
One responsibility per docstring: context assembly, memory enforcement, BOM intent
detection, infrastructure profiling, specialist-question management, and sufficiency
checks. No I/O in this file (no HTTP, no file reads) except OCI Object Store calls
already wrapped by `document_store` / `context_store`.

Move ALL of the following functions verbatim from `orchestrator_agent.py`:

**BOM intent and structured-BOM helpers (current lines ~1714–2264):**
- `_build_context_summary_for_skills`
- `_is_bom_deictic_followup`
- `_has_meaningful_decision_context`
- `_build_bom_followup_prompt`
- `_attach_structured_bom_inputs`
- `_build_structured_bom_inputs`
- `_structured_bom_region`
- `_structured_bom_architecture_option`
- `_structured_bom_native_target_services`
- `_structured_bom_native_workload_mapping`
- `_structured_bom_ocpu`
- `_structured_bom_memory_gb`
- `_structured_bom_block_tb`
- `_structured_bom_connectivity`
- `_structured_bom_dr`
- `_structured_bom_gpu_requested`
- `_capacity_match_to_gb`
- `_ram_capacity_match_to_gb`
- `_extract_block_storage_tb_from_text`
- `_merge_structured_bom_dicts`
- `_text_has_bom_sizing`

**BOM followup, memory enforcement, hydration (current lines ~2272–2769):**
- `_bom_followup_should_hydrate_from_context`
- `_is_bom_revision_request`
- `_append_reusable_bom_inputs`
- `_record_saved_note_context`
- `_enforce_memory_contract_on_tool_args`
- `_memory_facts_used`
- `_memory_latest_baseline_used`
- `_hydrate_tool_args_from_context`

**Specialist questions and sufficiency checks (current lines ~2800–3105):**
- `_normalize_specialist_question`
- `_stable_specialist_question_id`
- `_pov_has_sufficient_context`
- `_pov_targeted_questions`
- `_terraform_scope_is_bounded`
- `_terraform_targeted_questions`
- `_diagram_has_sufficient_context`
- `_specialist_question_bundle_from_result`
- `_should_ignore_specialist_question`
- `_latest_resolved_answer_map`
- `_resolved_answer_for_question`
- `_infer_bom_question_id`

**Region and infrastructure profile (current lines ~3148–3662):**
- `_record_region_constraint_if_present`
- `_record_infrastructure_profile_if_present`
- `_extract_infrastructure_profile`
- `_infrastructure_profile_context_lines`
- `_infrastructure_profile_ocpu_answer`
- `_infrastructure_profile_storage_answer`
- `_infrastructure_profile_connectivity_answer`
- `_infrastructure_profile_sizing_answer`
- `_infrastructure_profile_memory_answer`
- `_extract_memory_answer`
- `_extract_block_storage_answer`
- `_infer_components_scope_from_context`

**Answer resolution and decision context (current lines ~3890–4362):**
- `_apply_resolved_answers_to_tool_args`
- `_decision_context_with_auto_answers`
- `_mediate_specialist_questions`
- `_build_specialist_question_checkpoint`
- `_is_specialist_question_approve_message`
- `_specialist_question_id_map`
- `_normalize_specialist_question_id`
- `_specialist_question_id_aliases`
- `_message_supersedes_pending_specialist_questions`
- `_is_specialist_question_retry_message`
- `_handle_pending_specialist_questions`

**Decision context block and merge (current lines ~5856–6617):**
- `_build_decision_context_block`
- `_decision_context_hash`
- `_merge_decision_context`

**BOM call classification (current lines ~7473):**
- `_bom_call_was_memory_revision`

---

## Files to modify

### `agent/orchestrator_agent.py`
1. Delete every function listed above from this file.
2. Add this import near the top (after the existing imports):
   ```python
   import agent.archie_memory as archie_memory
   ```
3. Every call site in `orchestrator_agent.py` that previously called one of the
   moved functions directly (e.g. `_pov_has_sufficient_context(...)`) must now
   call it via the module: `archie_memory._pov_has_sufficient_context(...)`.
   Use `grep -n "function_name"` to find every call site.

### Tests that use `monkeypatch.setattr(orchestrator_agent, "X", ...)`
where `X` is a function now in `archie_memory`:

Update those patches to target the new module:
```python
# Before
monkeypatch.setattr(orchestrator_agent, "_pov_has_sufficient_context", fake)
# After
import agent.archie_memory as archie_memory
monkeypatch.setattr(archie_memory, "_pov_has_sufficient_context", fake)
```

The affected test files are most likely:
- `tests/test_specialist_mode_routing.py`
- `tests/test_orchestrator_decision_flow.py`
- `tests/test_orchestrator_refinement_flow.py`
- `tests/test_orchestrator_parallel_reply.py`

Run `grep -rn "_pov_has_sufficient_context\|_build_context_summary_for_skills\|_diagram_has_sufficient_context\|_enforce_memory_contract\|_hydrate_tool_args\|_mediate_specialist_questions\|_handle_pending_specialist_questions\|_bom_followup_should_hydrate" tests/` to find all affected patch sites.

---

## Files to NOT touch
- Any file in `sub_agents/`
- `agent/archie_loop.py` (does not exist yet — do not create it in this task)
- `drawing_agent_server.py`
- `agent/bom_service.py`, `agent/pov_agent.py`, `agent/jep_agent.py`, `agent/waf_agent.py`

---

## What to do
1. Create `agent/archie_memory.py`. Open with:
   ```python
   """
   archie_memory.py
   ----------------
   Context assembly, memory enforcement, BOM intent detection,
   infrastructure profiling, and specialist-question management for Archie.

   Called by archie_loop.py. Does not call run_turn or _execute_tool.
   """
   from __future__ import annotations
   ```
   Then add all required imports (copy from orchestrator_agent.py — only the ones
   these functions actually use).

2. Move the functions listed above verbatim into `archie_memory.py`.

3. In `orchestrator_agent.py`, add `import agent.archie_memory as archie_memory`
   and replace every direct call to a moved function with `archie_memory.X(...)`.

4. Run `python3.11 -m compileall agent/archie_memory.py agent/orchestrator_agent.py`
   and fix any import errors.

5. Run `grep -n "def _pov_has_sufficient_context\|def _build_context_summary\|def _hydrate_tool_args" agent/orchestrator_agent.py`
   — must return nothing.

6. Update test monkeypatch sites as described above.

7. Run `pytest tests/ -v -m "not live"`. Must be 9 or fewer failures (the pre-existing
   version-string failures). No new failures allowed.

---

## Acceptance criteria
- `python3.11 -m compileall agent/archie_memory.py` exits 0
- `grep -n "def _pov_has_sufficient_context" agent/orchestrator_agent.py` returns nothing
- `grep -n "def _hydrate_tool_args_from_context" agent/orchestrator_agent.py` returns nothing
- `grep -n "def _build_context_summary_for_skills" agent/orchestrator_agent.py` returns nothing
- `pytest tests/ -v -m "not live"` — no new failures beyond the 9 pre-existing ones
- `wc -l agent/orchestrator_agent.py` reports at least 4,000 fewer lines than before this PR

# Task: Delete dead BOM helpers from archie_memory.py
Phase: 4
Status: todo
Depends on: p4-cleanup.md merged to main

## Goal
Remove the ~49 structured-BOM helper functions from `agent/archie_memory.py`
that are no longer called anywhere. BOM generation is now handled by the BOM
sub-agent; these helpers are dead code.

---

## Prerequisite check — run first

For every function listed below, confirm it has zero callers outside
`archie_memory.py` itself:

```bash
grep -rn \
  "_structured_bom_region\|_structured_bom_architecture_option\|_structured_bom_native_target\|\
_structured_bom_native_workload\|_structured_bom_ocpu\|_structured_bom_memory_gb\|\
_structured_bom_block_tb\|_structured_bom_connectivity\|_structured_bom_dr\|\
_structured_bom_gpu_requested\|_capacity_match_to_gb\|_ram_capacity_match_to_gb\|\
_extract_block_storage_tb_from_text\|_merge_structured_bom_dicts\|_text_has_bom_sizing\|\
_build_structured_bom_inputs\|_attach_structured_bom_inputs\|_build_bom_followup_prompt\|\
_has_meaningful_decision_context\|_is_bom_deictic_followup\|\
_bom_call_was_memory_revision\|_structured_bom_region" \
  --include="*.py" . | grep -v "agent/archie_memory.py"
```

If any function has a caller outside `archie_memory.py`, **do not delete it**.
Note the hit in the PR body and leave the function in place.

---

## Functions to delete (if confirmed dead)

All of the following live in `agent/archie_memory.py`. Delete each one that
the prerequisite check confirms has no external callers:

**Structured BOM builders (called only by _build_structured_bom_inputs):**
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

**BOM orchestration helpers (called only by deleted functions or by archie_loop
BOM paths that no longer exist):**
- `_build_structured_bom_inputs`
- `_attach_structured_bom_inputs`
- `_build_bom_followup_prompt`
- `_has_meaningful_decision_context`
- `_is_bom_deictic_followup`
- `_bom_call_was_memory_revision`
- `_is_bom_revision_request`
- `_append_reusable_bom_inputs`
- `_bom_followup_should_hydrate_from_context` (if no external callers)

**Do not delete:**
- `_build_context_summary_for_skills` — called by archie_loop.py
- `_record_saved_note_context` — called by archie_loop.py
- `_enforce_memory_contract_on_tool_args` — called by archie_loop.py
- `_hydrate_tool_args_from_context` — called by archie_loop.py
- Any specialist-question function
- Any infrastructure-profile function
- Any decision-context function
- Anything the grep check shows has a live caller

---

## Files to NOT touch

- `agent/archie_loop.py`
- `agent/orchestrator_agent.py`
- `agent/hat_engine.py`
- `agent/safety_rules.py`
- `drawing_agent_server.py`
- Anything in `sub_agents/`
- Any test file (unless a test directly tests a deleted function — delete only
  that test, not the whole file)

---

## What to do

1. Run the prerequisite grep check.
2. Delete each confirmed-dead function from `agent/archie_memory.py`.
3. Remove any imports inside `archie_memory.py` that are only used by deleted
   functions.
4. Run: `python3.11 -m compileall agent/archie_memory.py` — must exit 0.
5. Run: `python3.11 -c "import agent.archie_memory"` — must exit 0.
6. Run: `pytest tests/ -v -m "not live"` — must be 0 failures.
7. Open a PR. Do not merge. Note in the PR body:
   - How many functions were deleted
   - The before/after line count of archie_memory.py
   - Any functions that had live callers and were kept

---

## Acceptance criteria

- `python3.11 -m compileall agent/archie_memory.py` exits 0
- `python3.11 -c "import agent.archie_memory"` exits 0
- `grep -n "def _structured_bom_region\|def _build_structured_bom_inputs\|def _is_bom_deictic_followup" agent/archie_memory.py` returns nothing
- `wc -l agent/archie_memory.py` is at least 500 lines shorter than before
- `pytest tests/ -v -m "not live"` — 0 failures

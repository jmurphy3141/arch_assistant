# Task: Phase 4a — delete provably-dead files and fix version assertions
Phase: 4
Status: todo
Depends on: p3-wire-hats.md merged to main

## Goal
Delete files that have zero live imports and no runtime role. Fix the 4
pre-existing version-assertion test failures. Make targeted doc updates.
No behavior change. No replacement logic. No touching the skill engine.

---

## Prerequisite checks — run these first, act on results

For each file to be deleted, confirm it has no live imports before touching it:

```bash
grep -rn "langgraph_orchestrator" --include="*.py" . \
    | grep -v "agent/langgraph_orchestrator.py"

grep -rn "langgraph_specialists" --include="*.py" . \
    | grep -v "agent/langgraph_specialists.py"

grep -rn "from agent.graphs\|import agent.graphs\|agent\.graphs\." \
    --include="*.py" . | grep -v "agent/graphs/"
```

If any grep returns a hit, **stop and note it in the PR** — do not delete that
file. Only delete files confirmed as unreferenced.

---

## Files to delete

Delete only these — nothing else:

- `agent/langgraph_orchestrator.py`
- `agent/langgraph_specialists.py`
- `agent/graphs/diagram_graph.py`
- `agent/graphs/jep_graph.py`
- `agent/graphs/pov_graph.py`
- `agent/graphs/terraform_graph.py`
- `agent/graphs/waf_graph.py`
- `agent/graphs/__init__.py` (if it exists)
- `SESSION_CHECKPOINT.md`

**Do NOT touch:**
- `agent/orchestrator_skill_engine.py`
- `agent/skill_loader.py`
- `gstack_skills/`
- `agent/orchestrator_skills/`
- Anything in `agent/hats/`, `sub_agents/`, `tests/`
- `agent/archie_loop.py`, `agent/archie_memory.py`, `agent/orchestrator_agent.py`
- `drawing_agent_server.py`

---

## Fix pre-existing test failures

The 4 known failures compare `"1.5.0"` against the server's actual `"1.9.1"`:

- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_001_missing_hints_returns_clarification`
- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_002_duplicate_drha_returns_stub_box`
- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_003_split_workloads_page_width`
- `tests/test_a2a.py::TestAgentCard::test_card_has_required_fields`

For each: find the assertion comparing a version string to `"1.5.0"` and update
the expected value to `"1.9.1"`. Change nothing else in those test files.

---

## Files to update

### `AGENTS.md`
Make targeted edits only:
- Remove entries for `langgraph_orchestrator.py`, `langgraph_specialists.py`,
  `agent/graphs/`, `critic_agent.py`, `governor_agent.py`.
- Add one-line entries for `agent/hat_engine.py` and `agent/safety_rules.py`.
- Update `orchestrator_agent.py` entry to say "26-line thin shim; re-exports
  run_turn from archie_loop.py".
- Update `archie_loop.py` entry to say "ReAct loop, tool dispatch, hat wiring,
  intent classification".
- Do not rewrite sections that are still accurate.

### `CLAUDE.md`
Make targeted edits only to the architecture overview and repository structure
sections:
- Remove `langgraph_orchestrator.py`, `langgraph_specialists.py`, `graphs/`,
  `critic_agent.py`, `governor_agent.py` from the file tree.
- Add `archie_loop.py`, `hat_engine.py`, `safety_rules.py`.
- Update the `orchestrator_agent.py` description to "thin shim".
- Update the Known Debt section: remove items resolved by Phases 1–3.
- Do not rewrite the file from scratch.

---

## What to do

1. Run the prerequisite import checks.
2. Delete each confirmed-safe file.
3. Fix the 4 version assertions in test files.
4. Make the targeted AGENTS.md and CLAUDE.md edits.
5. Run: `python3.11 -m compileall agent/ drawing_agent_server.py` — must exit 0.
6. Run: `pytest tests/ -v -m "not live"` — must be **0 failures**.
7. Open a PR. Do not merge. Add notes against each acceptance criterion.

---

## Acceptance criteria

- `python3.11 -m compileall agent/ drawing_agent_server.py` exits 0
- `ls agent/langgraph_orchestrator.py agent/langgraph_specialists.py` — both "No such file"
- `ls agent/graphs/` — "No such file or directory"
- `ls SESSION_CHECKPOINT.md` — "No such file"
- `agent/orchestrator_skill_engine.py` still exists (not touched)
- `agent/skill_loader.py` still exists (not touched)
- `pytest tests/ -v -m "not live"` — **0 failures**
- `AGENTS.md` no longer mentions deleted files
- `CLAUDE.md` no longer mentions deleted files

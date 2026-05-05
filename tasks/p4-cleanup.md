# Task: Phase 4 cleanup — delete dead code and update docs
Phase: 4
Status: todo
Depends on: p3-wire-hats.md merged to main

## Goal
Delete every file that is now dead after Phases 1–3, fix the 4 pre-existing test
failures, and update AGENTS.md and CLAUDE.md to reflect the final module structure.

---

## Prerequisite check

Before deleting anything, run the following import checks and confirm each returns
no hits from outside the file being deleted:

```bash
grep -rn "from agent.orchestrator_skill_engine\|import orchestrator_skill_engine" \
    --include="*.py" . | grep -v orchestrator_skill_engine.py

grep -rn "from agent.skill_loader\|import skill_loader\|from agent import skill_loader" \
    --include="*.py" . | grep -v skill_loader.py

grep -rn "from agent.langgraph_orchestrator\|import langgraph_orchestrator" \
    --include="*.py" . | grep -v langgraph_orchestrator.py

grep -rn "from agent.langgraph_specialists\|import langgraph_specialists" \
    --include="*.py" . | grep -v langgraph_specialists.py

grep -rn "from agent.graphs\|import agent.graphs\|agent\.graphs\." \
    --include="*.py" . | grep -v "agent/graphs/"
```

If any grep returns a hit outside the deleted file itself, you must **remove that
import and any code that depends on it** before deleting. Document each removal
in the PR body.

If `agent/archie_loop.py` still calls `OrchestratorSkillEngine` or `skill_loader`,
remove those calls: the hat engine (`agent/hat_engine.py`) is the replacement.
See the "Skill engine removal" section below for the safe removal pattern.

---

## Files to delete

Delete each file only after the prerequisite import check confirms it is safe:

- `agent/orchestrator_skill_engine.py`
- `agent/skill_loader.py`
- `agent/langgraph_orchestrator.py`
- `agent/langgraph_specialists.py`
- `agent/graphs/diagram_graph.py`
- `agent/graphs/jep_graph.py`
- `agent/graphs/pov_graph.py`
- `agent/graphs/terraform_graph.py`
- `agent/graphs/waf_graph.py`
- `agent/graphs/__init__.py` (if it exists)
- `SESSION_CHECKPOINT.md`
- `agent/orchestrator_skills/` directory — all files inside it
- `gstack_skills/` directory — all files inside it (content already captured in
  sub-agent system prompts and hats; these are no longer loaded at runtime)

**Do NOT delete** any file in `sub_agents/`, `agent/hats/`, `tests/`, or the
core agent pipeline (`bom_parser.py`, `layout_engine.py`, `intent_compiler.py`,
`drawio_generator.py`, `document_store.py`, `context_store.py`, etc.).

Before deleting `gstack_skills/` and `agent/orchestrator_skills/`, confirm:
```bash
grep -rn "gstack_skills\|orchestrator_skills" --include="*.py" . \
    | grep -v "skill_loader.py\|orchestrator_skill_engine.py"
```
If any live Python file references these directories (other than the files being
deleted), do not delete them — note in the PR instead.

---

## Skill engine removal (if archie_loop.py still uses it)

If `agent/archie_loop.py` still has:
```python
from agent.orchestrator_skill_engine import OrchestratorSkillEngine, OrchestratorSkillDecision
_SKILL_ENGINE = OrchestratorSkillEngine()
```

Replace the usage as follows:

1. Remove the `OrchestratorSkillEngine` import and `_SKILL_ENGINE` module-level
   variable.
2. Find every call to `_SKILL_ENGINE.preflight(...)` and
   `_SKILL_ENGINE.postflight(...)` in `run_turn()`. Replace them with a no-op
   that returns the permissive default:
   ```python
   # preflight replacement:
   skill_decision = OrchestratorSkillDecision(allow=True, block_reason="", preflight_notes="", postflight_notes="")
   # postflight replacement: nothing (skip entirely)
   ```
3. Remove the `OrchestratorSkillDecision` import — replace any reference to it
   with a simple `dataclass` or `SimpleNamespace` defined inline, or just inline
   the values.
4. Remove any import of `skill_loader` (discover_skills, select_skills_for_call)
   and any call site that uses it. Hat injection via `hat_engine.inject_hats()` is
   the replacement.
5. Verify compile: `python3.11 -m compileall agent/archie_loop.py`

---

## Fix pre-existing test failures

The 4 known pre-existing failures are `agentVersion` / `agent_version` assertions
that compare `"1.5.0"` against the server's actual `"1.9.1"`:

- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_001_missing_hints_returns_clarification`
- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_002_duplicate_drha_returns_stub_box`
- `tests/scenarios/test_scenarios.py::TestMultiRegion::test_mr_003_split_workloads_page_width`
- `tests/test_a2a.py::TestAgentCard::test_card_has_required_fields`

For each failure:
1. Find the assertion that compares a version string to `"1.5.0"`.
2. Update the expected value to `"1.9.1"` (the current server version).
3. Do not change any other assertion in those tests.

---

## Files to update

### `AGENTS.md`
Update to reflect Phase 4 final state:
- Remove any mention of `orchestrator_skill_engine.py`, `skill_loader.py`,
  `langgraph_orchestrator.py`, `langgraph_specialists.py`, `agent/graphs/`,
  `critic_agent.py`, `governor_agent.py`.
- Add `agent/hat_engine.py` with a one-line description.
- Add `agent/safety_rules.py` with a one-line description.
- Update the `agent/orchestrator_agent.py` entry to say "thin compatibility shim".
- Update the `agent/archie_loop.py` entry to say "ReAct loop, tool dispatch,
  intent classification (moved here from orchestrator_agent.py in Phase 2)".
- Update the `agent/archie_memory.py` entry to say "context assembly, memory
  enforcement, BOM hydration, specialist-question management".

### `CLAUDE.md`
Update the architecture overview and repository structure sections to reflect:
- `orchestrator_agent.py` is now a 26-line thin shim
- `archie_loop.py` is the actual ReAct loop
- `archie_memory.py` holds memory/context helpers
- `hat_engine.py` drives the hat system
- `safety_rules.py` holds deterministic safety checks
- `critic_agent.py` and `governor_agent.py` are gone
- `orchestrator_skill_engine.py` and `skill_loader.py` are gone
- `langgraph_*` and `agent/graphs/` are gone
- Sub-agents are now in `sub_agents/` (list all 6)

Do not rewrite CLAUDE.md from scratch — make targeted edits to the relevant
sections. The Known Debt section should be updated to remove items that are now
resolved.

---

## What to do

1. Run the import checks above for every file to be deleted.
2. If `archie_loop.py` imports `orchestrator_skill_engine` or `skill_loader`,
   apply the skill engine removal steps.
3. Delete each safe-to-delete file.
4. Fix the 4 pre-existing test version assertions.
5. Update `AGENTS.md` and `CLAUDE.md` as described.
6. Run: `python3.11 -m compileall agent/archie_loop.py agent/archie_memory.py \
   agent/orchestrator_agent.py drawing_agent_server.py`
7. Run: `pytest tests/ -v -m "not live"` — must be 0 failures.
8. Open a PR. Do not merge. Add notes against each acceptance criterion.

---

## Files to NOT touch

- `agent/archie_loop.py` — only modify if skill engine removal is required
- `agent/archie_memory.py`
- `agent/hat_engine.py`
- `agent/safety_rules.py`
- `agent/hats/*.md`
- `agent/sub_agent_client.py`
- Anything in `sub_agents/`
- `drawing_agent_server.py`
- Any test file except the 4 version-assertion fixes

---

## Acceptance criteria

- `python3.11 -m compileall agent/archie_loop.py agent/archie_memory.py agent/orchestrator_agent.py drawing_agent_server.py` exits 0
- `python3.11 -c "import agent.archie_loop; import agent.orchestrator_agent"` exits 0
- `ls agent/orchestrator_skill_engine.py` returns "No such file"
- `ls agent/skill_loader.py` returns "No such file"
- `ls agent/langgraph_orchestrator.py agent/langgraph_specialists.py` returns "No such file" for both
- `ls agent/graphs/` returns "No such file or directory"
- `ls SESSION_CHECKPOINT.md` returns "No such file"
- `pytest tests/ -v -m "not live"` — **0 failures** (the 4 pre-existing version failures are fixed)
- `grep -rn "orchestrator_skill_engine\|skill_loader" agent/archie_loop.py` returns nothing
- `AGENTS.md` and `CLAUDE.md` no longer mention deleted files

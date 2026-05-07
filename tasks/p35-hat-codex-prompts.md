# Phase 3.5 Hat Enhancement — Codex Prompts

---

## Prompt 1 — p35j: Expert Hat Injection (run first, alone)

```
Implement tasks/p35j-expert-hat-injection.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p35j origin/main

Then run the prerequisite check:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement in this order:

1. Rewrite all 6 hat files with YAML frontmatter + structured sections.
   Write the EXACT file contents specified in the spec for each of:
     agent/hats/bom_reviewer.md
     agent/hats/diagram_builder.md
     agent/hats/waf_reviewer.md
     agent/hats/terraform_reviewer.md
     agent/hats/critic.md
     agent/hats/governor.md
   Each file must have: --- YAML frontmatter --- then markdown with
   ## Core Principles, ## Quality Bar, ## Output Contract,
   ## Critic Evaluation Guidance, ## Failure Questions, ## Activation & Drop.

2. In agent/hat_engine.py, add:
   - Import yaml at top (import yaml as _yaml)
   - _hat_path(name) helper that returns the path to a hat's .md file or None
   - _parse_hat_file(path) function that splits YAML frontmatter from body
     and extracts H2 sections into a dict
   - build_expert_block(name) method on HatEngine that builds:
       [ACTIVE EXPERT: {display_name} v{version}]
       ## Core Principles
       ...
       [End ACTIVE EXPERT: {display_name}]
   - get_hat_meta(name) method returning parsed frontmatter dict

3. In skillforge/forge.py, add:
   - _build_active_system_msg(active_hats) method that prepends expert blocks
     to the base system message when hats are active
   - Replace all self._get_system_msg() calls inside run_turn with
     self._build_active_system_msg(active_hats)
   - Update _run_critique_pass to accept active_hats param and use
     _build_active_system_msg

Verify all acceptance criteria:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  grep "ACTIVE EXPERT" agent/hat_engine.py
  grep "_build_active_system_msg" skillforge/forge.py
  python3.11 -c "
from agent.hat_engine import HatEngine
h = HatEngine()
block = h.build_expert_block('bom_reviewer')
assert '[ACTIVE EXPERT: BOM Expert v1.0]' in block
assert '## Core Principles' in block
print('OK')
"
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p35j: expert prompt injection — structured hat files + [ACTIVE EXPERT] system prompt prefix
Push to branch claude/p35j.
```

---

## Prompt 2a — p35k: Hat Stack & Transition Rules (run after p35j merges)

```
Implement tasks/p35k-hat-stack-rules.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p35k origin/main

Verify p35j is present:
  grep "build_expert_block" agent/hat_engine.py   # must match

Then run:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. Add hat_rules YAML blocks to all 6 hat files (replacing hat_rules: {}).
   Use the EXACT content from the spec for each hat file.

2. In agent/hat_engine.py, add:
   - get_hat_rules(name) → returns meta.get("hat_rules", {})
   - get_transition_suggestions(active_hats, turn_message) → scan
     when_to_activate triggers for all non-active hats, return matches
   - get_suggested_next_hat(name) → returns suggested_next_hat or None

3. In skillforge/forge.py:
   - Before the ReAct loop in run_turn, call get_transition_suggestions
     and yield a "status" TurnEvent if suggestions are non-empty
   - When a hat is dropped, call get_suggested_next_hat and yield a
     "status" TurnEvent if non-null

Verify:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  grep "hat_rules" agent/hats/bom_reviewer.md
  grep "get_transition_suggestions" agent/hat_engine.py
  grep "Suggested hats" skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p35k: hat_rules frontmatter + transition suggestions in HatEngine and Forge
Push to branch claude/p35k.
```

---

## Prompt 2b — p35l: Hat-Specific Memory Views (run after p35j merges, parallel with p35k)

```
Implement tasks/p35l-hat-memory-views.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p35l origin/main

Verify p35j is present:
  grep "_parse_hat_file" agent/hat_engine.py   # must match

Then run:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py skillforge/memory.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. Add memory_focus YAML blocks to all 6 hat files (replacing memory_focus: {}).
   Use the EXACT content from the spec for each hat file.

2. In agent/hat_engine.py, add:
   - get_memory_focus(name) → returns meta.get("memory_focus", {})
   - build_memory_view_block(name, memory_snapshot) → builds
     [MEMORY VIEW FOR {EXPERT}] ... [End MEMORY VIEW] block
     filtering raw memory by priority_fields, or full if include_full_memory=True
   - get_hat_meta(name) if not already present from p35j (add if missing)

3. In skillforge/forge.py:
   - After assembling the memory snapshot, if active_hats is non-empty,
     build memory view blocks for each active hat
   - Prepend these blocks to the user prompt (before inject_hats call)
   - After each memory_contract tool result updates the snapshot, rebuild
     the memory prefix for the next iteration

Do NOT modify skillforge/memory.py.

Verify:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  grep "memory_focus" agent/hats/bom_reviewer.md
  grep "build_memory_view_block" agent/hat_engine.py
  grep "MEMORY VIEW" skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p35l: memory_focus frontmatter + hat-specific memory view injection
Push to branch claude/p35l.
```

---

## Prompt 3 — p35m: Declarative Coordination Rules (run after p35k and p35l merge)

```
Implement tasks/p35m-hat-coordination-rules.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p35m origin/main

Verify p35k and p35l are present:
  grep "get_transition_suggestions" agent/hat_engine.py   # p35k
  grep "build_memory_view_block" agent/hat_engine.py      # p35l

Then run:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Implement:

1. Add coordination YAML blocks to all 6 hat files (replacing coordination: {}).
   Use the EXACT content from the spec for each hat file.

2. In agent/hat_engine.py, add:
   - get_coordination_rules(name) → returns meta.get("coordination", {})
   - get_parallel_hats(name) → returns coordination.get("parallel_with", [])
   - get_handoff_message(name) → returns coordination.get("handoff_message") or None

3. In skillforge/forge.py:
   - After the existing transition suggestions check (from p35k), add coordination
     trigger check for active hats — emit status events for recommended_hats
     and parallel_with opportunities
   - When a hat is dropped, emit handoff_message status event if non-null
   - Log synthesis_step at DEBUG when non-null
   - Log hat transitions (activate + drop) at INFO level

Verify:
  python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
  grep "coordination" agent/hats/bom_reviewer.md
  grep "get_coordination_rules" agent/hat_engine.py
  grep "Coordination:" skillforge/forge.py
  grep "Hat transition:" skillforge/forge.py
  pytest tests/ -q --tb=short 2>&1 | tail -5

Commit message: p35m: coordination rules frontmatter + parallel/handoff/synthesis events in Forge
Push to branch claude/p35m.
```

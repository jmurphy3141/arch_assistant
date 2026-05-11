# Phase 3.9 Codex Prompts — Enhanced Manager Reasoning Loop

Run order: **p39a + p39b in parallel** → **p39c after both complete**.

p39a and p39b touch entirely different files (one new skill file, one set of
hat files). They can run simultaneously. p39c modifies `skillforge/forge.py`
and depends on the skill file and hat sections created by p39a and p39b.

---

## Prompt 1 — p39a: Manager Reasoning Loop Skill File

```
Implement tasks/p39a-manager-reasoning-loop-skill.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p39a origin/main

Prerequisite check:
  ls skills/
  cat skills/intent_routing.md | head -5

Create skills/manager_reasoning_loop.md with the exact content specified in
tasks/p39a-manager-reasoning-loop-skill.md. The spec contains the complete
file as a fenced code block. Write exactly that content.

Verify:
  grep "Step 1\|Step 2\|Step 3\|Step 4\|Step 5\|Step 6" skills/manager_reasoning_loop.md | wc -l
  grep "Pre-Action Checklist\|Post-Action Review" skills/manager_reasoning_loop.md
  python3.11 -m compileall skillforge/forge.py
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39a: create skills/manager_reasoning_loop.md — 6-step reasoning scaffold
Branch: claude/p39a. Push when done.
```

---

## Prompt 2 — p39b: Hat Pre/Post-Action Sections

```
Implement tasks/p39b-hat-pre-post-action-sections.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p39b origin/main

Prerequisite check:
  ls agent/hats/
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"

Read tasks/p39b-hat-pre-post-action-sections.md in full before writing anything.
The spec provides exact section content for each of the 8 hat files.

For EACH hat file, append BOTH new sections to the END of the file
(after all existing content). Do NOT modify existing content.

The 8 hat files are:
  agent/hats/oci_bom_expert.md
  agent/hats/diagram_for_oci.md
  agent/hats/terraform_for_oci.md
  agent/hats/oci_waf_reviewer.md
  agent/hats/oci_customer_pov_writer.md
  agent/hats/jep_writer.md
  agent/hats/critic.md
  agent/hats/governor.md

Verify:
  for f in agent/hats/*.md; do
    echo -n "$f: "
    grep -c "Pre-Action Checklist\|Post-Action Review" "$f"
  done
  python3.11 -c "
  import agent.hat_engine as h
  hats = h.load_hats()
  assert len(hats) == 8, f'Expected 8 hats, got {len(hats)}'
  print('p39b hat load OK:', sorted(hats.keys()))
  "
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39b: add Pre-Action Checklist + Post-Action Review to all 8 hats
Branch: claude/p39b. Push when done.
```

---

## Prompt 3 — p39c: Forge Structured Loop (run AFTER p39a and p39b merge)

```
Implement tasks/p39c-forge-structured-loop.md exactly as written.

First sync: merge p39a and p39b into a working branch, or branch from main
and apply all three task changes:
  git fetch origin
  git checkout -b claude/p39c origin/main

  # Apply p39a change (new file only — copy from p39a branch or recreate):
  # skills/manager_reasoning_loop.md must exist

  # Apply p39b changes (hat file additions — copy from p39b branch or recreate):
  # All 8 hats must have ## Pre-Action Checklist and ## Post-Action Review

  # Then apply p39c changes to skillforge/forge.py

Read tasks/p39c-forge-structured-loop.md carefully. It specifies:
  1. A new private method _run_planning_call() in the Forge class
  2. A new private method _run_expert_review() in the Forge class
  3. Wiring of _run_planning_call() into run_turn() BEFORE the ReAct loop
  4. Wiring of _run_expert_review() into run_turn() BEFORE _run_critique_pass()

Implementation rules:
  - Add both methods near _run_critique_pass() (around line 725)
  - _run_planning_call fires when expert hats active, before the iteration loop
  - _run_expert_review fires when spec.critique_enabled and result.status == "ok",
    immediately before the existing _run_critique_pass call
  - If the LLM call inside either method raises, log and return prompt unchanged
  - Neither method changes _MANUAL_ONLY_HATS, __init__, or the public API

Verify:
  python3.11 -m compileall skillforge/forge.py

  grep "_run_planning_call\|planning_call\|PLANNING:" skillforge/forge.py
  grep "_run_expert_review\|EXPERT_REVIEW\|Post-Action" skillforge/forge.py

  python3.11 -c "
  import inspect, skillforge.forge as f
  src = inspect.getsource(f.Forge.run_turn)
  expert_pos = src.index('_run_expert_review')
  critic_pos = src.index('_run_critique_pass')
  assert expert_pos < critic_pos, 'Expert review must fire before critic'
  print('ordering OK')
  "

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
      def apply_hat(self, hats, name): return hats
      def drop_hat(self, hats, name): return hats
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
  assert result.reply == 'plain reply', f'Got: {result.reply}'
  print('no-hat run_turn OK')
  "

  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39c: Forge planning call (Steps 1–3) + expert self-review (Step 6) before critic
Branch: claude/p39c. Push when done.
```

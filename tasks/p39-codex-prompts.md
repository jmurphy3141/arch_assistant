# Phase 3.9 Codex Prompts — Enhanced Manager Reasoning Loop

Run order: **p39a → p39b → p39c** (sequential — each builds on the previous).

The manager (Archie / Forge) wears the hats. Hats are not passed to sub-agents.
Sub-agents are execution engines. The manager reasons as the expert (Step 4)
and reviews as the expert (Step 6).

---

## Prompt 1 — p39a: 6-Step Loop + Expert Pre-Action

```
Implement tasks/p39a-manager-reasoning-loop-skill.md exactly as written.

First sync to latest main:
  git fetch origin && git checkout -b claude/p39a origin/main

Prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  ls skills/
  grep "_run_expert_pre_action" skillforge/forge.py  # must be zero

Read tasks/p39a-manager-reasoning-loop-skill.md in full before writing anything.

Part 1 — create skills/manager_reasoning_loop.md with the exact content
specified in the task spec (the complete file is in a fenced code block).

Part 2 — add _run_expert_pre_action() private method to the Forge class in
skillforge/forge.py, and wire it into run_turn() before the spec.handler()
call, gated on spec.critique_enabled.

Key rule: the method fires when the manager (Forge) is about to call a
critique-enabled tool AND an expert hat is active. It makes an LLM call
with the full [ACTIVE EXPERT] system prompt, logs the reasoning at INFO
level, and appends EXPERT_THINKING: to the running prompt.

Verify:
  grep "Step 1\|Step 2\|Step 3\|Step 4\|Step 5\|Step 6" skills/manager_reasoning_loop.md | wc -l
  grep "YOU wear the hat\|Sub-agents execute" skills/manager_reasoning_loop.md
  grep "_run_expert_pre_action" skillforge/forge.py | wc -l
  grep "logger.info.*Expert pre-action" skillforge/forge.py
  python3.11 -m compileall skillforge/forge.py
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39a: 6-step manager reasoning loop — skill file + expert pre-action thinking (Step 4)
Branch: claude/p39a. Push when done.
```

---

## Prompt 2 — p39b: Hat Pre/Post-Action Sections (run after p39a merges)

```
Implement tasks/p39b-hat-pre-post-action-sections.md exactly as written.

First sync from p39a:
  git fetch origin && git checkout -b claude/p39b origin/claude/p39a

  (Or, if p39a has been merged to main:)
  git fetch origin && git checkout -b claude/p39b origin/main

Prerequisite check:
  ls agent/hats/
  python3.11 -c "import agent.hat_engine as h; print(sorted(h.load_hats().keys()))"
  for f in agent/hats/*.md; do echo -n "$f: "; grep -c "Pre-Action Checklist\|Post-Action Review" "$f"; done
  # all must print 0 before the edit

Read tasks/p39b-hat-pre-post-action-sections.md in full before writing anything.
The spec provides exact section content for all 8 hat files.

For EACH of the 8 hat files, append BOTH new sections to the END of the file.
Do NOT modify any existing content.

The 8 hat files:
  agent/hats/oci_bom_expert.md
  agent/hats/diagram_for_oci.md
  agent/hats/terraform_for_oci.md
  agent/hats/oci_waf_reviewer.md
  agent/hats/oci_customer_pov_writer.md
  agent/hats/jep_writer.md
  agent/hats/critic.md
  agent/hats/governor.md

Verify:
  for f in agent/hats/*.md; do echo -n "$f: "; grep -c "Pre-Action Checklist\|Post-Action Review" "$f"; done
  # every file must print 2

  python3.11 -c "
  import agent.hat_engine as h
  hats = h.load_hats()
  assert len(hats) == 8, f'Expected 8, got {len(hats)}'
  print('OK:', sorted(hats.keys()))
  "
  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39b: add expert Pre-Action Checklist + Post-Action Review to all 8 hats
Branch: claude/p39b. Push when done.
```

---

## Prompt 3 — p39c: Expert Post-Review with Iterate/Surface Decision (run after p39b merges)

```
Implement tasks/p39c-forge-structured-loop.md exactly as written.

First sync from p39b:
  git fetch origin && git checkout -b claude/p39c origin/claude/p39b

  (Or, if p39b has been merged to main:)
  git fetch origin && git checkout -b claude/p39c origin/main

Prerequisite check:
  python3.11 -m compileall skillforge/forge.py
  grep "_run_expert_pre_action" skillforge/forge.py | wc -l   # must be ≥ 2 (from p39a)
  grep "_run_expert_post_review" skillforge/forge.py           # must be zero before edit

Read tasks/p39c-forge-structured-loop.md in full before writing anything.

Add _run_expert_post_review() to the Forge class in skillforge/forge.py.
The method returns (updated_prompt, decision) where decision is one of:
  "approved" — all checks pass → fire critic
  "iterate"  — fixable gap → continue loop without critic
  "surface"  — unfixable gap → return directly to user

Wire _run_expert_post_review() into run_turn() inside the
"if spec.critique_enabled and result.status == 'ok':" block:
  1. Call _run_expert_post_review first
  2. If decision == "surface" → set reply and break
  3. If decision == "iterate" → continue loop
  4. If decision == "approved" → call _run_critique_pass as before

Define three module-level constants: _EXPERT_REVIEW_APPROVED, _EXPERT_REVIEW_ITERATE,
_EXPERT_REVIEW_SURFACE.

Verify:
  python3.11 -m compileall skillforge/forge.py

  grep "_run_expert_post_review" skillforge/forge.py | wc -l
  # must be ≥ 2

  python3.11 -c "
  import inspect, skillforge.forge as f
  src = inspect.getsource(f.Forge.run_turn)
  post_pos = src.index('_run_expert_post_review')
  critic_pos = src.index('_run_critique_pass')
  assert post_pos < critic_pos
  print('ordering OK')
  "

  python3.11 -c "
  import asyncio
  from skillforge.forge import Forge
  from skillforge.types import MemorySnapshot

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
  assert result.reply == 'plain reply'
  print('no-hat path OK')
  "

  pytest tests/test_forge.py -q --tb=short 2>&1 | tail -5

Commit message: p39c: expert post-review (Step 6) with iterate/surface/approve decision before critic
Branch: claude/p39c. Push when done.
```

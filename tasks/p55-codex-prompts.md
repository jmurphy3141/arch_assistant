# p55 Codex Prompts — POC Workflow

## Background

p55 adds the full POC workflow to Archie: background job execution so SEs can
kick off generation during a meeting, a POC Strategist that explores 3 parallel
options and recommends the best fit, parallel artifact fan-out once the SE
confirms, and a PowerPoint deck as the final client deliverable.

Run order: p55a → p55f (cleanup). p55b → p55c. p55f → p55d and p55e.
p55b/p55c and p55a/p55f can run in parallel with each other.
p55d requires p55b + p55c + p55f. p55e requires p55f.

Port assignments (do not reuse):
- 8082–8089: taken (see config.yaml)
- poc_strategist: 8090
- presentation: 8091

---

## p55a — Background Job Support + Telegram Notification

```
Read tasks/p55a-background-jobs.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p55a origin/main

Run the prerequisite check first:
  python3.11 -m compileall skillforge/forge.py drawing_agent_server.py agent/notifications.py
  grep "run_turn_background\|/api/chat/background" skillforge/forge.py drawing_agent_server.py
  # must be zero matches
  grep "TODO\|pass  # not implemented" agent/notifications.py
  # should show the stub at lines 73-75

Read these files before implementing:
  skillforge/forge.py         — find run_turn() signature; add run_turn_background() after it
  drawing_agent_server.py     — search _new_job, _complete_job, _fail_job (lines 334-360);
                                search /api/job/{job_id} (~line 2436) to understand existing pattern
  agent/notifications.py      — read lines 62-75 to see the TODO stub
  agent/archie_session.py     — confirm it is a thin wrapper; do NOT add logic here
  ui/src/components/ChatInterface.tsx — understand current send flow before adding toggle

Implement in this order:
1. skillforge/forge.py — add async run_turn_background(message, history, context,
   on_complete, on_error); wraps run_turn(); no Archie-specific logic
2. drawing_agent_server.py — add POST /api/chat/background endpoint; calls _new_job(),
   spawns asyncio.create_task, returns 202 with job_id immediately
3. agent/notifications.py — implement _send_telegram() using httpx; fire-and-forget;
   read token/chat_id from env vars named in config.yaml
4. config.yaml — add telegram: {enabled: false, bot_token_env: TELEGRAM_BOT_TOKEN,
   chat_id_env: TELEGRAM_CHAT_ID}
5. ui/src/components/ChatInterface.tsx — background toggle; poll GET /api/job/{id}
   every 5s; append reply on complete; show error toast on failure

Run ALL acceptance criteria checks before committing:
  python3.11 -m compileall skillforge/forge.py drawing_agent_server.py agent/notifications.py
  grep "run_turn_background" skillforge/forge.py  # must show definition
  grep "/api/chat/background" drawing_agent_server.py  # must show route
  pytest tests/test_background_job.py -v  # create this file per spec

Commit message:
p55a: background chat job support and Telegram notification

Branch: claude/p55a (from main). Push when done.
```

---

## p55b — POC Strategist: Sub-Agent + Handler + Hat

```
Read tasks/p55b-poc-strategist.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p55b origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/tools/specialists.py agent/archie_wiring.py
  grep "PocStrategistHandler\|generate_poc_plan\|oci_poc_strategist" \
    agent/tools/specialists.py agent/archie_wiring.py
  # must be zero matches
  ls agent/hats/oci_poc_strategist.md 2>/dev/null && echo EXISTS || echo MISSING
  # must print MISSING
  ls sub_agents/poc_strategist/ 2>/dev/null && echo EXISTS || echo MISSING
  # must print MISSING

Reference patterns — read these before implementing:
  sub_agents/pov/server.py          — copy this structure exactly
  sub_agents/pov/config.yaml        — copy; change name and port to 8090
  agent/tools/specialists.py        — _SpecialistHandler.__call__() lines 64-243
  agent/hats/oci_bom_expert.md      — follow this format exactly (YAML + 10 sections)
  agent/archie_wiring.py            — build_forge(); see existing forge.register_tool() calls
  agent/sub_agent_client.py         — call_sub_agent() signature

Implement in this order:
1. sub_agents/poc_strategist/ — create server.py, system_prompt.md, config.yaml (port 8090),
   __init__.py; system prompt instructs the agent to return ONE scored POC option as raw JSON
2. agent/tools/specialists.py — add PocStrategistHandler class; use asyncio.gather() for
   3 parallel calls with angles: migration_modernization, performance_scale_ai,
   cost_optimization_tco; return_exceptions=True; skip failed angles gracefully;
   rank by relevance_score / max(executability_hours, 1)
3. agent/hats/oci_poc_strategist.md — YAML frontmatter + 10 sections matching oci_bom_expert.md
   format; Pre-Action Checklist fires NEEDS_CLARIFICATION if pain_statement absent
4. agent/archie_wiring.py — import PocStrategistHandler; register generate_poc_plan with
   requires_hat="oci_poc_strategist"; add POC workflow rule to _TOOL_SEQUENCING_RULES
5. config.yaml — add poc_strategist: "http://localhost:8090" under sub_agents

Run ALL acceptance criteria checks before committing:
  python3.11 -m compileall agent/tools/specialists.py agent/archie_wiring.py \
    sub_agents/poc_strategist/server.py
  grep "PocStrategistHandler" agent/tools/specialists.py  # must show class definition
  grep "generate_poc_plan" agent/archie_wiring.py         # must show register_tool call
  grep "poc_strategist" config.yaml                       # must show port 8090
  python3.11 -c "import agent.hats; print('ok')"
  pytest tests/test_poc_strategist.py -v  # create this file per spec

Commit message:
p55b: POC Strategist — 3 parallel option exploration sub-agent, handler, and hat

Branch: claude/p55b (from main). Push when done.
```

---

## p55c — Parallel Artifact Fan-out After POC Confirmation

```
Read tasks/p55c-artifact-fanout.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main AFTER p55b is merged.

  git fetch origin
  git checkout -b claude/p55c origin/main

Run the prerequisite check first:
  python3.11 -m compileall agent/tools/specialists.py
  grep "PocStrategistHandler" agent/tools/specialists.py  # must show class (from p55b)
  grep "_detect_poc_confirmation\|_build_fanout_result\|status.*parallel" agent/tools/specialists.py
  # must be zero matches

Read these before implementing:
  agent/tools/specialists.py   — PocStrategistHandler.__call__() from p55b
  skillforge/types.py          — find ParallelToolCall definition
  skillforge/forge.py          — search "parallel" near lines 745-786 to confirm the dispatch path

Changes are limited to agent/tools/specialists.py only. Do NOT touch skillforge/forge.py.

Implement:
1. Add _detect_poc_confirmation(user_message, memory) at module level:
   - check re.search patterns for "option 1/2/3", "go with", "proceed", "confirm", "let's do"
   - look up poc_options from memory.decision_context
   - return the matched option dict or None; default ambiguous matches to index 0
2. Add _build_fanout_result(self, option, memory) method on PocStrategistHandler:
   - return ToolResult(status="parallel", parallel_tools=[...]) with 5 ParallelToolCall entries:
     generate_diagram, generate_bom, generate_jep, generate_terraform, generate_presentation
   - hydrate each tool's _user_message from option fields
3. At top of PocStrategistHandler.__call__(): detect confirmation first; if confirmed,
   return _build_fanout_result(); otherwise fall through to existing exploration path
4. Add "import re" and "from skillforge.types import ParallelToolCall" if not present

Edge cases:
  - poc_options absent from memory + confirmation intent → ToolResult(status="needs_input",
    clarification="Please generate a POC plan first with generate_poc_plan.")
  - All 5 parallel tool names must match exactly what is registered in archie_wiring.py

Run ALL acceptance criteria checks before committing:
  python3.11 -m compileall agent/tools/specialists.py
  grep "_detect_poc_confirmation\|_build_fanout_result" agent/tools/specialists.py
  pytest tests/test_poc_strategist.py -v -k "confirmation or fanout or parallel"

Commit message:
p55c: parallel artifact fan-out — all 5 artifacts generated concurrently on POC confirmation

Branch: claude/p55c (from main, after p55b merged). Push when done.
```

---

## p55d — Archie System Prompt: POC Pattern Recognition + Workflow Sequencing

```
Read tasks/p55d-archie-system-prompt.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main AFTER p55b, p55c, and p55f are merged.

  git fetch origin
  git checkout -b claude/p55d origin/main

Run the prerequisite check first:
  python3.11 -m py_compile agent/archie_wiring.py
  grep "POC Planning Workflow\|POC PATTERN RECOGNITION\|confirmed_option_name" agent/archie_wiring.py
  # must be zero matches

Only one file changes: agent/archie_wiring.py. Both changes are string appends
inside existing triple-quoted strings — do not restructure or reformat existing content.

Read agent/archie_wiring.py first:
  - Find _EXPERT_IDENTITY (search for "## Expert Identity") — note the closing triple-quote
  - Find _TOOL_SEQUENCING_RULES (search for "## Tool Sequencing Rules") — note the closing triple-quote

Implement in this order:
1. agent/archie_wiring.py — append POC PATTERN RECOGNITION + POC RISK INSTINCT +
   PROACTIVE RECOMMENDATIONS block to _EXPERT_IDENTITY (before closing triple-quote)
2. agent/archie_wiring.py — append ### POC Planning Workflow section to
   _TOOL_SEQUENCING_RULES (before closing triple-quote); include all 8 numbered rules

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile agent/archie_wiring.py

  python3.11 -c "
  from pathlib import Path
  src = Path('agent/archie_wiring.py').read_text()
  checks = [
      ('POC Planning Workflow',            'workflow section header'),
      ('action=\"confirm\"',               'confirm mode reference'),
      ('confirmed_option_name',            'confirmed_option_name arg'),
      ('Do NOT generate artifacts before', 'no-artifacts-before-confirm rule'),
      ('stored procedures are the silent', 'proactive recommendation'),
      ('Compatibility Checker',            'specific proactive tip'),
      ('Wrong audience',                   'risk instinct: audience mismatch'),
      ('POC PATTERN RECOGNITION',          'pattern recognition block'),
  ]
  for content, label in checks:
      assert content in src, f'FAIL: {label!r} missing'
  print('PASS')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

Commit message:
p55d: POC workflow sequencing and pattern recognition in Archie system prompt

Branch: claude/p55d (from main, after p55b + p55c + p55f merged). Push when done.
```

---

## p55e — PowerPoint Presentation Generation

```
Read tasks/p55e-powerpoint.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main AFTER p55f is merged.

  git fetch origin
  git checkout -b claude/p55e origin/main

Run the prerequisite check first:
  grep "python-pptx" requirements.txt && echo FOUND || echo MISSING  # must print MISSING
  ls sub_agents/presentation/ 2>/dev/null && echo EXISTS || echo MISSING  # must print MISSING
  ls agent/tools/presentation.py 2>/dev/null && echo EXISTS || echo MISSING  # must print MISSING
  grep "generate_presentation" agent/archie_wiring.py && echo FOUND || echo MISSING  # must print MISSING

Reference patterns — read these before implementing:
  sub_agents/pov/server.py          — copy this structure exactly
  agent/tools/specialists.py        — _SpecialistHandler pattern for the handler
  agent/hats/oci_bom_expert.md      — hat format (YAML + 10 sections)
  agent/archie_wiring.py            — see existing register_tool() calls
  drawing_agent_server.py           — search "Content-Type" in /download handler

Implement in this order:
1. requirements.txt — add python-pptx>=1.0.2
2. sub_agents/presentation/assets/ — if oracle-oci-architecture-toolkit-v24.1.pptx is not
   available, create a placeholder PPTX with one comment slide; document where to source it
3. sub_agents/presentation/scripts/resolve_oci_powerpoint_icon.py — OCI_ICON_MAP dict +
   resolve_icon(service_name) function
4. sub_agents/presentation/scripts/render_oci_powerpoint.py — render(spec, output_path)
   producing exactly 7 slides; use toolkit PPTX for icon shapes via _copy_icon_from_toolkit()
5. sub_agents/presentation/server.py — A2A handler; parse engagement_context for spec fields;
   call render(); encode PPTX bytes as base64 in response; port 8091
6. sub_agents/presentation/system_prompt.md, config.yaml (port 8091), __init__.py
7. agent/tools/presentation.py — PresentationHandler; hydrate from memory; call sub-agent;
   decode base64; save bytes via document_store.save_doc(); key: presentation/{customer_id}/v1.pptx
8. agent/hats/oci_presentation_writer.md — YAML + 10 sections; Pre-Action fires
   NEEDS_CLARIFICATION if poc_recommendation absent; parallel_with all 4 other artifact tools
9. agent/archie_wiring.py — import PresentationHandler; register generate_presentation with
   requires_hat="oci_presentation_writer"
10. config.yaml — add presentation: "http://localhost:8091" under sub_agents
11. drawing_agent_server.py — in /download handler, add .pptx branch with Content-Type:
    application/vnd.openxmlformats-officedocument.presentationml.presentation

Run ALL acceptance criteria checks before committing:
  python3.11 -m compileall agent/tools/presentation.py agent/archie_wiring.py \
    sub_agents/presentation/server.py \
    sub_agents/presentation/scripts/render_oci_powerpoint.py
  grep "python-pptx" requirements.txt          # must show version
  grep "generate_presentation" agent/archie_wiring.py  # must show register_tool call
  grep "presentation" config.yaml              # must show port 8091
  python3.11 -c "
from sub_agents.presentation.scripts import render_oci_powerpoint
import tempfile, os
spec = {'poc_name': 'Test', 'customer_name': 'Acme', 'pain_statement': '',
        'oci_services': [], 'bom_summary': '', 'jep_phases': [], 'date': 'Jan 1 2026'}
with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as f:
    path = f.name
render_oci_powerpoint.render(spec, path)
from pptx import Presentation
prs = Presentation(path)
assert len(prs.slides) == 7, f'Expected 7 slides, got {len(prs.slides)}'
os.unlink(path)
print('7-slide check passed')
"
  pytest tests/test_presentation.py -v  # create this file per spec

  # Verify the p55c placeholder swap
  grep "generate_presentation" agent/tools/specialists.py  # must show in _build_fanout_result
  grep "generate_presentation" tests/test_poc_strategist.py  # must show in tool names test

Commit message:
p55e: PowerPoint presentation generation — 7-slide Oracle-standard POC deck with OCI stencils

Branch: claude/p55e (from main, after p55f merged). Push when done.
```

## p55f — Background Job Cleanup (Post-p55a Review)

```
Read tasks/p55f-background-cleanup.md carefully end to end before touching any code.

IMPORTANT: Branch from origin/main AFTER p55a is merged.

  git fetch origin
  git checkout -b claude/p55f origin/main

Run the prerequisite check first:
  python3.11 -m compileall drawing_agent_server.py agent/notifications.py
  grep "run_turn_background" drawing_agent_server.py  # must be zero matches (it's unused today)

Three focused fixes — one file each. Read the relevant section before each fix.

Fix 1: drawing_agent_server.py
  Read _run_background_chat() in the /api/chat/background endpoint.
  Read how the session/forge instance is obtained (search _get_or_create or equivalent).
  Restructure _run_background_chat() so it calls session.forge.run_turn_background()
  with on_complete and on_error callbacks. Move _persist_bom_xlsx_downloads,
  _build_artifact_manifest, _persist_chat_project_membership, and notify() into on_complete.

Fix 2: agent/notifications.py
  Load config once in _send() and pass cfg dict as second arg to _send_telegram().
  Update _send_telegram(message, cfg) to accept cfg instead of re-loading it.
  If tests/test_background_job.py calls _send_telegram directly, update that too.

Fix 3: ui/src/components/ChatInterface.tsx
  In the backgroundJobId polling useEffect, add setBackgroundMode(false) in both
  the success path and the error path — immediately after setBackgroundJobId(null).

Run ALL acceptance criteria checks before committing:
  python3.11 -m compileall drawing_agent_server.py agent/notifications.py
  grep "run_turn_background" drawing_agent_server.py  # must show call site now
  grep "_send_telegram" agent/notifications.py        # must show two-arg call
  cd ui && npm run typecheck
  pytest tests/test_background_job.py -v

Commit message:
p55f: background job cleanup — wire run_turn_background, single config load, one-shot mode

Branch: claude/p55f (from main, after p55a merged). Push when done.
```

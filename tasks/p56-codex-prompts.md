# p56 Codex Prompts — Archie Behavior Polish

## Background

p56 fixes six behavior issues found during a live RGA session on 2026-05-27.
All are self-contained changes to existing files — no new sub-agents or framework
changes except p56c (Forge step3 injection) and p56f (poc_strategist server).

Run order: p56a + p56b + p56d + p56f in parallel → p56c after p56a+p56b merge,
p56e after p56d merges.

---

## p56a — Fix artifact verification false positive on "uploaded"

```
Read tasks/p56-archie-behavior.md section p56a before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p56a origin/main

Run the prerequisite check first:
  python3.11 -m py_compile agent/archie_session.py
  python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_session import _ACTION_VERIFY_MARKERS
print('Current markers:', _ACTION_VERIFY_MARKERS)
print('uploaded present:', 'uploaded' in _ACTION_VERIFY_MARKERS)
"
  # 'uploaded present: True' confirms the bug exists — proceed

Read these before implementing:
  agent/archie_session.py  — search _ACTION_VERIFY_MARKERS (near line 1998)
                           — search _ACTION_PRODUCTION_MARKERS nearby
                           — search _tool_backed_action_intent and _build_artifact_verification_reply
                             to understand how these tuples are used

Changes are limited to agent/archie_session.py only.

Implement:
1. Remove "uploaded" from _ACTION_VERIFY_MARKERS.
2. While in this area, audit both _ACTION_VERIFY_MARKERS and _ACTION_PRODUCTION_MARKERS
   for other single-word terms that appear naturally in conversational sentences
   (e.g., "present", "ready", "file"). Remove any that would false-positive on
   normal messages like "I've uploaded notes" or "ready to present to the customer".
   Keep specific multi-word phrases — they are safe.

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile agent/archie_session.py

  python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_session import _ACTION_VERIFY_MARKERS
assert 'uploaded' not in _ACTION_VERIFY_MARKERS, 'uploaded still in list'
print('PASS: uploaded removed')
"

  python3.11 -c "
msg = \" i've just uploaded my meeting notes (rga.pdf). please save them. \"
markers = ('in the bucket', 'in object storage', 'verify', 'verify file',
           'verify files', 'check file', 'check files', 'list files', 'list the files')
verification = any(m in msg for m in markers)
assert not verification, f'false positive: {[m for m in markers if m in msg]}'
print('PASS: no false positive on upload notes message')
"

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5

Commit message:
p56a: fix artifact verification false positive — remove "uploaded" from _ACTION_VERIFY_MARKERS

Branch: claude/p56a (from main). Push when done.
```

---

## p56b — Move RESPONSE RULES to top of _EXPERT_IDENTITY

```
Read tasks/p56-archie-behavior.md section p56b before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p56b origin/main

Run the prerequisite check first:
  python3.11 -m py_compile agent/archie_wiring.py
  python3.11 -c "
from pathlib import Path
src = Path('agent/archie_wiring.py').read_text()
# RESPONSE STYLE exists but is buried — should fail this check
try:
    rp = src.index('RESPONSE RULES')
    sp = src.index('senior OCI Solutions Architect')
    print('RESPONSE RULES pos:', rp, '  senior OCI pos:', sp)
    print('Rules before identity?', rp < sp)
except ValueError as e:
    print('Not found:', e)
"
  # 'Rules before identity? False' confirms the bug — proceed

Only one file changes: agent/archie_wiring.py.

Read agent/archie_wiring.py first:
  - Search _EXPERT_IDENTITY (the triple-quoted string starting with "## Expert Identity")
  - Find the current RESPONSE STYLE block (search "RESPONSE STYLE") — note its position
  - Find "senior OCI Solutions Architect" — this is where identity content begins
  - Note the exact opening of _EXPERT_IDENTITY after the triple-quote

Implement in this order:
1. Insert a new RESPONSE RULES block at the very start of _EXPERT_IDENTITY content,
   before "senior OCI Solutions Architect" or any other identity text. Use this exact text:

   RESPONSE RULES (apply to every reply without exception):
   - You are a teammate having a working conversation. Not a document generator.
   - Short direct answers to questions. No tables, no headers, no bullet storms.
   - No emoji anywhere. Ever.
   - Do not draft customer emails, formal documents, or structured reports unless
     the user explicitly asks ("write an email", "draft the JEP", "make a table").
   - If your response has headers or more than 6 bullet points, it is too long.
     Rewrite it as 2-3 sentences.
   - Do not end responses with "Thoughts?", "Let me know!", or tool-call prompts
     unless you produced something for review.

2. Remove the existing RESPONSE STYLE block from its current mid-block position
   (it was added in PR #248 — search "RESPONSE STYLE:" to find it).
   The new RESPONSE RULES block at the top replaces it entirely.

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile agent/archie_wiring.py

  python3.11 -c "
from pathlib import Path
src = Path('agent/archie_wiring.py').read_text()
response_rules_pos = src.index('RESPONSE RULES')
senior_pos = src.index('senior OCI Solutions Architect')
assert response_rules_pos < senior_pos, \
    f'RESPONSE RULES ({response_rules_pos}) must come before senior OCI ({senior_pos})'
assert 'No emoji anywhere' in src, 'emoji rule missing'
assert 'teammate having a working conversation' in src, 'teammate rule missing'
assert 'RESPONSE STYLE' not in src, 'old RESPONSE STYLE block not removed'
print('PASS')
"

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5

Commit message:
p56b: move RESPONSE RULES to top of _EXPERT_IDENTITY — short answers, no emoji, no unsolicited docs

Branch: claude/p56b (from main). Push when done.
```

---

## p56c — Forge step3 POC intent injection

```
Read tasks/p56-archie-behavior.md section p56c before touching any code.

IMPORTANT: Branch from origin/main AFTER p56a and p56b are merged.

  git fetch origin
  git checkout -b claude/p56c origin/main

Run the prerequisite check first:
  python3.11 -m py_compile skillforge/forge.py
  grep "_plan_has_poc_intent\|_memory_has_poc_recommendation\|FORGE OVERRIDE.*poc" skillforge/forge.py
  # must be zero matches

Read these files before implementing:
  skillforge/forge.py      — search "step3" to find the planning phase; understand
                             where plan_output is produced and where tool dispatch begins
  skillforge/types.py      — MemorySnapshot structure
  agent/archie_wiring.py   — confirm poc_recommendation and poc_options are memory fields

Changes are limited to skillforge/forge.py only. Do NOT add any OCI or Archie
domain knowledge to forge.py — the helpers must be domain-agnostic string/dict checks.

Implement:
1. Add two module-level private helpers (below imports, before the Forge class):

   def _plan_has_poc_intent(plan: str) -> bool:
       keywords = ("poc", "proof of concept", "demo", "pilot", "what to build",
                   "generate_poc_plan", "options", "evaluate")
       plan_lower = plan.lower()
       return sum(1 for k in keywords if k in plan_lower) >= 2

   def _memory_has_poc_recommendation(memory) -> bool:
       if not memory:
           return False
       dc = getattr(memory, "decision_context", {}) or {}
       return bool(dc.get("poc_recommendation") or dc.get("poc_options"))

2. In the step3 planning phase of Forge.run_turn(), after plan_output is produced
   and before the tool-dispatch LLM call, add:

   if _plan_has_poc_intent(plan_output) and not _memory_has_poc_recommendation(memory):
       plan_output = (
           plan_output
           + "\n[FORGE OVERRIDE: poc_recommendation absent — "
           "call generate_poc_plan(action='explore') now]"
       )

   The override text goes into the planning context visible to the next LLM call
   (tool dispatch), not into the user-visible reply. This is the same mechanism
   as correction propagation added in p43d.

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile skillforge/forge.py

  python3.11 -c "
plan = 'Customer has Oracle RAC cost pain. I should evaluate poc options and demo path.'
keywords = ('poc','proof of concept','demo','pilot','what to build',
            'generate_poc_plan','options','evaluate')
count = sum(1 for k in keywords if k in plan.lower())
assert count >= 2, f'only {count} keywords matched'
print('PASS: poc intent detected')
"

  grep "_plan_has_poc_intent\|_memory_has_poc_recommendation" skillforge/forge.py
  grep "FORGE OVERRIDE.*poc_recommendation" skillforge/forge.py

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5

Commit message:
p56c: Forge step3 injects generate_poc_plan override when poc intent present and no recommendation in memory

Branch: claude/p56c (from main, after p56a and p56b merged). Push when done.
```

---

## p56d — Systemd service units for poc_strategist and presentation sub-agents

```
Read tasks/p56-archie-behavior.md section p56d before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p56d origin/main

Run the prerequisite check first:
  ls deploy/oci-poc-strategist.service 2>/dev/null && echo EXISTS || echo MISSING
  ls deploy/oci-presentation.service 2>/dev/null && echo EXISTS || echo MISSING
  # both must print MISSING
  cat deploy/oci-agent.service   # read existing unit file as reference for format
  cat deploy/README.md           # read to understand where to add port table entries

Two new files to create, one file to update.

Implement:
1. deploy/oci-poc-strategist.service — new systemd unit file:

   [Unit]
   Description=Archie POC Strategist Sub-Agent
   After=network.target

   [Service]
   User=opc
   WorkingDirectory=/home/opc/drawing-agent
   ExecStart=/usr/bin/python3.11 -m sub_agents.poc_strategist.server
   Restart=always
   StandardOutput=append:/home/opc/drawing-agent/poc_strategist.log
   StandardError=append:/home/opc/drawing-agent/poc_strategist.log

   [Install]
   WantedBy=multi-user.target

2. deploy/oci-presentation.service — new systemd unit file (same pattern, port 8091):

   [Unit]
   Description=Archie Presentation Sub-Agent
   After=network.target

   [Service]
   User=opc
   WorkingDirectory=/home/opc/drawing-agent
   ExecStart=/usr/bin/python3.11 -m sub_agents.presentation.server
   Restart=always
   StandardOutput=append:/home/opc/drawing-agent/presentation.log
   StandardError=append:/home/opc/drawing-agent/presentation.log

   [Install]
   WantedBy=multi-user.target

3. deploy/README.md — add to the port table:
   - Port 8090: poc_strategist sub-agent
   - Port 8091: presentation sub-agent
   Add a "Sub-agent services" section with systemd install steps:
     sudo cp deploy/oci-poc-strategist.service /etc/systemd/system/
     sudo cp deploy/oci-presentation.service /etc/systemd/system/
     sudo systemctl daemon-reload
     sudo systemctl enable --now oci-poc-strategist oci-presentation

Run ALL acceptance criteria checks before committing:
  ls deploy/oci-poc-strategist.service deploy/oci-presentation.service
  grep "8090\|poc_strategist" deploy/README.md
  grep "8091\|presentation" deploy/README.md
  grep "ExecStart" deploy/oci-poc-strategist.service deploy/oci-presentation.service

Commit message:
p56d: add systemd service units for poc_strategist (8090) and presentation (8091) sub-agents

Branch: claude/p56d (from main). Push when done.
```

---

## p56e — UI tool waiting labels for POC sub-agent calls

```
Read tasks/p56-archie-behavior.md section p56e before touching any code.

IMPORTANT: Branch from origin/main AFTER p56d is merged.

  git fetch origin
  git checkout -b claude/p56e origin/main

Run the prerequisite check first:
  python3.11 -m py_compile drawing_agent_server.py
  grep "generate_poc_plan\|generate_presentation" drawing_agent_server.py | grep -i "waiting\|label\|hat"
  # must be zero matches

Read drawing_agent_server.py first:
  - Search _TOOL_WAITING_LABELS to find the dict (near line 3187)
  - Read a few existing entries to understand the (hat_name, description) tuple format
  - Search tool_started to confirm how the label reaches the SSE event

Then check the UI wiring:
  grep -n "tool_started\|thinkingStatus" ui/src/components/ChatInterface.tsx | head -20
  # if tool_started events update thinkingStatus, no UI changes needed

Changes required in drawing_agent_server.py. UI changes only if tool_started
is NOT surfaced as thinkingStatus in ChatInterface.tsx.

Implement:
1. Add two entries to _TOOL_WAITING_LABELS in drawing_agent_server.py:
   "generate_poc_plan":     ("POC Strategy", "POC strategist — 3 parallel evaluations"),
   "generate_presentation": ("Presentation", "presentation specialist"),

2. If the grep above shows tool_started events do NOT reach the UI thinkingStatus:
   In ChatInterface.tsx SSE event loop, add handling for tool_started events to
   display the message field as the working status text.

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile drawing_agent_server.py
  grep "generate_poc_plan.*POC Strategy\|POC strategist.*parallel" drawing_agent_server.py
  grep "generate_presentation.*Presentation\|presentation specialist" drawing_agent_server.py
  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5

Commit message:
p56e: add generate_poc_plan and generate_presentation to tool waiting labels — UI shows POC evaluation status

Branch: claude/p56e (from main, after p56d merged). Push when done.
```

---

## p56f — poc_strategist server: extract JSON from markdown LLM responses

```
Read tasks/p56-archie-behavior.md section p56f before touching any code.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p56f origin/main

Run the prerequisite check first — confirm the bug:
  python3.11 -m py_compile sub_agents/poc_strategist/server.py
  # manually curl to confirm LLM returns markdown (may need sub-agent running):
  # curl -s -X POST http://localhost:8090/a2a \
  #   -H "Content-Type: application/json" \
  #   -d '{"id":"t1","task":"Plan a POC","engagement_context":{"angle":"migration_modernization"}}' \
  #   | python3.11 -c "import sys,json; r=json.load(sys.stdin); print(r.get('result','')[:200])"

Read these before implementing:
  sub_agents/poc_strategist/server.py       — find the handle() function and run_inference call
  sub_agents/poc_strategist/system_prompt.md — current JSON instruction placement
  sub_agents/poc_strategist/config.yaml     — current temperature setting

Three focused fixes — one per file.

Implement in this order:
1. sub_agents/poc_strategist/server.py — add _extract_json() helper and call it
   after run_inference() in handle():

   import re as _re
   import json as _json_mod

   def _extract_json(text: str) -> str:
       text = text.strip()
       try:
           _json_mod.loads(text)
           return text
       except _json_mod.JSONDecodeError:
           pass
       match = _re.search(r'\{.*\}', text, _re.DOTALL)
       if match:
           candidate = match.group(0)
           try:
               _json_mod.loads(candidate)
               return candidate
           except _json_mod.JSONDecodeError:
               pass
       return text  # caller handles the error

   In handle(), after text = await anyio.to_thread.run_sync(...):
       text = _extract_json(text)

2. sub_agents/poc_strategist/config.yaml — change temperature from 0.6 to 0.1

3. sub_agents/poc_strategist/system_prompt.md — prepend this block at the very top
   of the file, before any other content:

   CRITICAL OUTPUT FORMAT: Return ONLY a raw JSON object. No markdown, no prose, no
   code fences. Your entire response must be parseable by json.loads(). Start with {
   and end with }. Any other format causes a system failure.

   Example output (abbreviated):
   {"option_name":"...", "relevance_score":8, "executability_hours":4, "cost_effectiveness":"...", "security_highlights":["..."], "wow_moment":"...", "demo_script_summary":"...", "oci_services":["..."]}

Run ALL acceptance criteria checks before committing:
  python3.11 -m py_compile sub_agents/poc_strategist/server.py

  python3.11 -c "
import re, json

text = '## Migration Modernization\n\nHere is my analysis:\n\n{\"option_name\": \"Migrate to OCI\", \"relevance_score\": 8, \"executability_hours\": 4}\n\nHope this helps!'

def extract_json(t):
    t = t.strip()
    try: json.loads(t); return t
    except json.JSONDecodeError: pass
    m = re.search(r'\{.*\}', t, re.DOTALL)
    if m:
        try: json.loads(m.group(0)); return m.group(0)
        except: pass
    return t

result = extract_json(text)
parsed = json.loads(result)
assert parsed['option_name'] == 'Migrate to OCI'
print('PASS: extracted JSON from markdown response')
"

  grep "0\.1" sub_agents/poc_strategist/config.yaml
  grep "CRITICAL OUTPUT FORMAT" sub_agents/poc_strategist/system_prompt.md

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5

Commit message:
p56f: poc_strategist server extracts JSON from markdown LLM responses — fixes "All 3 angles failed"

Branch: claude/p56f (from main). Push when done.
```

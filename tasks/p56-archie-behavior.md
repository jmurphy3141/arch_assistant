# Task p56 — Archie Behavior Polish

**Repository:** jmurphy3141/arch_assistant
**Branch:** claude/p56 (from main)
**Context:** Issues found during live RGA session on 2026-05-27

Five fixes. p56a, p56b, and p56d can run in parallel. p56c depends on p56a+p56b.
p56e depends on p56d.

---

## p56a — Fix artifact verification false positives

**Problem:** Two separate code paths in `agent/archie_session.py` produce the
canned response "I don't have a persisted artifact manifest to verify yet" on
completely unrelated messages:

1. `_ACTION_VERIFY_MARKERS` at line 1998 includes `"uploaded"`. Any message
   containing that word — including "I've just **uploaded** my meeting notes" —
   sets `verification=True` in `_tool_backed_action_intent()`, which then
   triggers `_build_artifact_verification_reply()` at line 2257.

2. `_is_explicit_artifact_verification_request()` at line 2115 includes
   `" present"` in `uploaded_state`. Fixed in PR #243 but a second variant
   remains: the `" uploaded"` marker in `_ACTION_VERIFY_MARKERS` (separate
   code path, same symptom).

**Root cause:** The upload-notes flow sends a message like "I've just uploaded
my meeting notes (filename.pdf). Please save them." The `save_notes` tool
sequencing rule in `archie_wiring.py` (line 171) correctly handles this case —
but the Python pre-check at line 2252 intercepts it first and short-circuits
to the verification reply before Forge ever sees the message.

**Fix:**

1. `agent/archie_session.py` line 1998 — remove `"uploaded"` from
   `_ACTION_VERIFY_MARKERS`:
   ```python
   # Before
   _ACTION_VERIFY_MARKERS = (
       "in the bucket",
       "in object storage",
       "uploaded",
       ...
   )
   # After — remove "uploaded"
   _ACTION_VERIFY_MARKERS = (
       "in the bucket",
       "in object storage",
       ...
   )
   ```

2. While in this area, audit the full `_ACTION_VERIFY_MARKERS` and
   `_ACTION_PRODUCTION_MARKERS` lists for other overly broad terms that
   could false-positive on conversational messages. Remove any single-word
   markers that appear in normal sentences without artifact context.

**Acceptance criteria:**
```bash
python3.11 -m py_compile agent/archie_session.py

# Must NOT trigger verification reply
python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_session import _ACTION_VERIFY_MARKERS
assert 'uploaded' not in _ACTION_VERIFY_MARKERS, 'uploaded still in list'
print('PASS')
"

# Simulate the upload message — verification must be False
python3.11 -c "
msg = \" i've just uploaded my meeting notes (rga.pdf). please save them. \"
markers = ('in the bucket', 'in object storage', 'verify', 'verify file',
           'verify files', 'check file', 'check files', 'list files', 'list the files')
verification = any(m in msg for m in markers)
assert not verification, f'false positive: {[m for m in markers if m in msg]}'
print('PASS: no false positive')
"

pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
```

**Commit message:**
```
p56a: fix artifact verification false positive — remove "uploaded" from _ACTION_VERIFY_MARKERS
```

**Branch:** `claude/p56a` from main.

---

## p56b — Archie response style enforcement

**Problem:** Archie ignores the RESPONSE STYLE rules added to `_EXPERT_IDENTITY`
in PR #248. When asked conversational questions ("what do we need from the
customer?"), it responds with full customer-facing email drafts, formatted tables,
emoji headers, and "Thoughts? 💡" closers. This persists across fresh sessions,
so it is not a conversation history contamination issue — the model's formatting
priors are stronger than the embedded instruction.

**Root cause:** The `_EXPERT_IDENTITY` block is ~80 lines long. The RESPONSE
STYLE section (added last) appears near the end of a dense system prompt block.
The model weights earlier, more prominent instructions more heavily. A buried
paragraph about style is not enough.

**Fix — two-part:**

**Part 1:** Move RESPONSE STYLE to the very top of `_EXPERT_IDENTITY`, before
all other sections, as a short hard rule:

```python
_EXPERT_IDENTITY = """
## Expert Identity

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

You are a senior OCI Solutions Architect...
[rest of identity unchanged]
"""
```

**Part 2:** Remove the RESPONSE STYLE block from its current position (mid-block,
after POC PATTERN RECOGNITION) since it is now at the top.

**Acceptance criteria:**
```bash
python3.11 -m py_compile agent/archie_wiring.py

python3.11 -c "
from pathlib import Path
src = Path('agent/archie_wiring.py').read_text()
# Rules must appear at the top of _EXPERT_IDENTITY, before 'senior OCI'
response_rules_pos = src.index('RESPONSE RULES')
senior_pos = src.index('senior OCI Solutions Architect')
assert response_rules_pos < senior_pos, 'RESPONSE RULES must come before senior OCI identity'
assert 'No emoji anywhere' in src
assert 'teammate having a working conversation' in src
print('PASS')
"

pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
```

**Commit message:**
```
p56b: move RESPONSE RULES to top of _EXPERT_IDENTITY — short answers, no emoji, no unsolicited docs
```

**Branch:** `claude/p56b` from main.

---

## p56c — Reliable `generate_poc_plan` triggering

**Problem:** Archie answers POC questions from its own LLM knowledge instead of
calling `generate_poc_plan`. The POC Planning Workflow in `_TOOL_SEQUENCING_RULES`
says to offer to run the tool when enough signal exists, but the model treats POC
discussion as a conversational turn and never reaches the offer. Even when the
user explicitly says "explore POC options," the model generates a formatted table
from knowledge instead of calling the tool.

**Root cause:** The model can always satisfy a POC question with its own knowledge,
so it never feels the need to call the tool. Prompt instructions alone cannot
reliably force tool calls when the model has a plausible conversational answer.

**Fix — Forge step3 planning injection:**

The right fix is domain-agnostic: if Forge's step3 planning output identifies a
POC intent AND `poc_recommendation` is absent from memory, inject a one-line
override before the tool-dispatch LLM call. This is orchestration (Forge owns
it), not routing logic (which belongs in Archie).

**`skillforge/forge.py`** — in the step3 planning phase, after parsing the plan:

```python
# After step3 planning returns, before tool dispatch:
if _plan_has_poc_intent(plan_output) and not _memory_has_poc_recommendation(memory):
    # Inject: force the offer or the tool call
    plan_output = plan_output + "\n[FORGE OVERRIDE: poc_recommendation absent — call generate_poc_plan(action='explore') now]"
```

Where:
```python
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
```

Note: the override text goes into the planning context visible to Forge's next
LLM call (tool-dispatch), not into the user-visible reply. This is the same
mechanism as correction propagation (p43d).

**Acceptance criteria:**
```bash
python3.11 -m py_compile skillforge/forge.py

# _plan_has_poc_intent must return True for POC-flavored plans
python3.11 -c "
import sys; sys.path.insert(0, '.')
# inline test since helper may be module-private
plan = 'Customer has Oracle RAC cost pain. I should evaluate poc options and demo path.'
keywords = ('poc','proof of concept','demo','pilot','what to build','generate_poc_plan','options','evaluate')
count = sum(1 for k in keywords if k in plan.lower())
assert count >= 2, f'only {count} keywords matched'
print('PASS: poc intent detected')
"

pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
```

**Commit message:**
```
p56c: Forge step3 injects generate_poc_plan override when poc intent present and no recommendation in memory
```

**Branch:** `claude/p56c` from main (after p56a and p56b merged, since p56c
touches forge.py and must not conflict).

---

---

## p56d — Sub-agent startup on server restart

**Problem:** `poc_strategist` (port 8090) and `presentation` (port 8091) are new
sub-agents added in p55b and p55e. They are independent processes that must be
started alongside the main server. Currently they have no startup wiring — if the
server restarts, both sub-agents die and all `generate_poc_plan` calls fail with
"All 3 POC exploration angles failed."

**Fix — two parts:**

**Part 1:** Add the two sub-agents to `deploy/oci-agent.service` (or create
companion unit files) so systemd starts and restarts them automatically:

```ini
# deploy/oci-poc-strategist.service
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
```

```ini
# deploy/oci-presentation.service
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
```

**Part 2:** Update `deploy/README.md` port table to include the two new sub-agents
(8090: poc_strategist, 8091: presentation) and document the systemd unit install
steps.

**Acceptance criteria:**
```bash
ls deploy/oci-poc-strategist.service deploy/oci-presentation.service
grep "8090\|poc_strategist" deploy/README.md
grep "8091\|presentation" deploy/README.md
```

**Commit message:**
```
p56d: add systemd service units for poc_strategist (8090) and presentation (8091) sub-agents
```

**Branch:** `claude/p56d` from main. Independent of p56a/b/c.

---

## p56e — UI progress indicator for POC sub-agent calls

**Problem:** When `generate_poc_plan` fires, the user sees a generic "Archie is
working..." spinner. There is no indication that 3 parallel sub-agent evaluations
are running or that results are coming from the `poc_strategist` specialist. The
existing `_TOOL_WAITING_LABELS` in `drawing_agent_server.py` (line 3187) has
entries for BOM, diagram, JEP etc. but not for `generate_poc_plan`.

**Fix — two parts:**

**Part 1:** Add `generate_poc_plan` and `generate_presentation` to
`_TOOL_WAITING_LABELS` in `drawing_agent_server.py`:

```python
_TOOL_WAITING_LABELS = {
    ...existing entries...
    "generate_poc_plan":     ("POC Strategy", "POC strategist — 3 parallel evaluations"),
    "generate_presentation": ("Presentation", "presentation specialist"),
}
```

**Part 2:** The `tool_started` SSE event is already wired through the streaming
endpoint and displayed in `ChatInterface.tsx` as the `thinkingStatus` / working
message. Verify the message reaches the UI by checking:

```bash
grep -n "tool_started\|thinkingStatus\|hat.*message\|message.*hat" \
  ui/src/components/ChatInterface.tsx | head -20
```

If `tool_started` events update `thinkingStatus`, no UI changes are needed —
just the server-side label addition is sufficient. If the message is not surfaced,
add handling in the SSE event loop in `ChatInterface.tsx` to display the
`message` field from `tool_started` events as the working status text.

**Acceptance criteria:**
```bash
python3.11 -m py_compile drawing_agent_server.py
grep "generate_poc_plan.*POC Strategy\|POC strategist" drawing_agent_server.py
# UI: trigger generate_poc_plan in a chat session
# Working status should read "Archie put on the POC Strategy hat and is calling
# the POC strategist — 3 parallel evaluations."
```

**Commit message:**
```
p56e: add generate_poc_plan and generate_presentation to tool waiting labels — UI shows POC evaluation status
```

**Branch:** `claude/p56e` from main (after p56d merged, since both touch server files).

---

## p56f — poc_strategist sub-agent: extract JSON from LLM markdown response

**Problem:** When `generate_poc_plan` is called, "All 3 POC exploration angles failed"
is returned every time. Root cause confirmed via direct curl to `http://localhost:8090/a2a`:
the OCI GenAI LLM ignores the "Return only a raw JSON object" instruction in the system
prompt and returns a full markdown essay (e.g., "## Migration Modernization\n\n**Overview**...").

`PocStrategistHandler.__call__()` in `agent/tools/specialists.py` calls
`json.loads(str(response.get("result") or ""))` which throws `JSONDecodeError` on the
markdown string. This is caught as a failure, all 3 angles fail, and the error message
is returned to the user.

**Root cause:** The OCI GenAI model at the sub-agent endpoint cannot be reliably forced
into JSON-only output via system prompt instructions alone.

**Fix — three parts:**

**Part 1:** In `sub_agents/poc_strategist/server.py`, add a JSON extraction step after
`run_inference()`. If the result is not valid JSON, attempt to extract the first `{...}`
block using regex before returning:

```python
import re, json as _json

def _extract_json(text: str) -> str:
    """Return text if it's already valid JSON, else extract the first {...} block."""
    text = text.strip()
    try:
        _json.loads(text)
        return text
    except _json.JSONDecodeError:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            _json.loads(candidate)
            return candidate
        except _json.JSONDecodeError:
            pass
    return text  # caller handles the error

# In handle():
text = await anyio.to_thread.run_sync(lambda: run_inference(...))
text = _extract_json(text)
return A2AResponse(result=text, status="ok", ...)
```

**Part 2:** Lower temperature in `sub_agents/poc_strategist/config.yaml` from `0.6` to
`0.1` — lower temperature makes the model more likely to follow format instructions.

**Part 3:** Strengthen the JSON instruction at the very top of
`sub_agents/poc_strategist/system_prompt.md` (before any other text):

```
CRITICAL OUTPUT FORMAT: Return ONLY a raw JSON object. No markdown, no prose, no
code fences. Your entire response must be parseable by json.loads(). Start with {
and end with }. Any other format will cause a system failure.

Example output format (abbreviated):
{"option_name":"...", "relevance_score":8, "executability_hours":4, "cost_effectiveness":"...", "security_highlights":["..."], "wow_moment":"...", "demo_script_summary":"...", "oci_services":["..."]}
```

**Acceptance criteria:**
```bash
python3.11 -m py_compile sub_agents/poc_strategist/server.py

# JSON extraction helper must work on markdown-wrapped responses
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
print('PASS: extracted JSON from markdown')
"

grep "0.1\|temperature" sub_agents/poc_strategist/config.yaml
grep "CRITICAL OUTPUT FORMAT" sub_agents/poc_strategist/system_prompt.md

pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -5
```

**Commit message:**
```
p56f: poc_strategist server extracts JSON from markdown LLM responses — fixes "All 3 angles failed"
```

**Branch:** `claude/p56f` from main. Independent — run in parallel with p56a/b/d.

---

## Run order

```
p56a  ──┐
p56b  ──┼──▶  merge all three  ──▶  p56c
p56d  ──┘                      ──▶  p56e (after p56d)

p56f  (independent — parallel with p56a/b/d)
```

p56a, p56b, p56d, and p56f are independent — run in parallel.
p56c branches from main after p56a and p56b are merged.
p56e branches from main after p56d is merged.

# p55 — POC Workflow: Strategy, Background Execution, Parallel Artifacts, PowerPoint

## Context

The SE team is reorganizing around technical POCs that close deals. The workflow gap:
Archie generates artifacts when asked but has no capability to drive from rough requirements
to a concrete, demonstrable proof. SEs need Archie to answer "what should we build?" before
building it, run in the background during meetings, generate all artifacts in parallel, and
produce a client-facing PowerPoint deck.

## 1:1 Gap Analysis

| Capability | Needed | Exists Today | Gap |
|---|---|---|---|
| POC option strategy | Given rough requirements, produce 3 ranked POC options scored on relevance, executability, cost, security | `infra_tech_research` answers "what services?" not "what POC closes this deal?" | **Critical** — no deal-strategy layer |
| Parallel exploration | 3 angles explored simultaneously (migration, AI/ML, cost) | Sequential sub-agent calls only | High — 3× slower |
| Background execution | Kick off during a meeting, notified when done | SSE must stay open for entire turn | **Critical** — blocks field use |
| Parallel artifact fan-out | All 5 artifacts generated simultaneously after POC confirmed | Sequential tool calls only | High — ~4 min → ~90 sec |
| PowerPoint generation | Client-facing 7-slide deck with Oracle OCI stencils | Zero PPTX capability | **Critical** — AEs need decks |
| Telegram notification | Push notification when background job completes | Stub only (TODO comment) | Medium — needed for background mode |

---

## 2. Prioritized Enhancement Plan

### P0 — Blocking for field use

**A. Background job support for chat** (`POST /api/chat/background`)
The current SSE model requires an open connection for the full turn. SEs cannot
kick off a POC generation and walk into a meeting. Infrastructure already exists
for BOM upload (`_JOB_STORE`, `_new_job()`, etc.) — extend to chat turns.

**B. POC Strategist** (`generate_poc_plan`)
No capability to answer "what should we build?". 3 parallel sub-agent calls
exploring migration, AI/ML, and cost angles. Scored on relevance, executability,
cost-effectiveness, and security. Recommended option includes demo script and
wow moment.

### P1 — Multiplier value

**C. Parallel artifact fan-out**
After POC confirmed, all 5 artifacts (diagram, BOM, JEP, Terraform, presentation)
start simultaneously via existing `asyncio.gather()` path in forge.py. Zero
framework changes — pure Archie wiring.

**D. PowerPoint generation** (`generate_presentation`)
Client-facing 7-slide deck using Oracle's `oracle-oci-architecture-toolkit-v24.1.pptx`
stencils. Same icon-from-template pattern as `OCI_Library.xml` for draw.io. Sub-agent
produces the deck; `python-pptx` renders it.

---

## 3. Architecture Boundary

| Layer | What changes |
|---|---|
| **Forge** (`skillforge/`) | `run_turn_background()` method only — domain-agnostic |
| **Archie** (`agent/`, `sub_agents/`) | All tools, hats, sub-agents, notification implementation |

Forge gets one new method. Everything else is Archie.

---

## Task p55a — Background chat job support + Telegram notification

```
Context: The /api/chat/stream SSE connection must stay open for the full turn.
SEs need to kick off POC generation during a meeting and receive a Telegram
notification when the work completes. Background job infrastructure already
exists for BOM upload — extend it to chat turns.

Existing reusable code (do NOT rebuild):
  drawing_agent_server.py lines 334-360: _JOB_STORE, _new_job(), _complete_job(), _fail_job()
  drawing_agent_server.py ~line 2436: GET /api/job/{job_id} polling endpoint
  agent/notifications.py lines 62-75: Telegram stub (TODO comment)

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p55a origin/main

---

CHANGE 1: skillforge/forge.py

Add this method to the Forge class (after run_turn):

```python
async def run_turn_background(
    self,
    message: str,
    history: list,
    context: dict,
    on_complete,
    on_error,
) -> None:
    """Run a turn in the background. Calls on_complete(TurnResult) or on_error(Exception)."""
    try:
        result = await self.run_turn(message, history, context)
        await on_complete(result)
    except Exception as exc:
        await on_error(exc)
```

No Archie-specific logic. Callbacks are injected by the caller.

---

CHANGE 2: drawing_agent_server.py

Add new endpoint after the existing /api/chat/stream endpoint:

```python
@app.post("/api/chat/background", status_code=202)
async def chat_background(request: ChatRequest):
    """Kick off a chat turn as a background job. Returns job_id immediately."""
    job_id = _new_job()

    async def on_complete(result):
        _complete_job(job_id, {
            "reply": result.reply,
            "artifacts": result.artifacts,
            "history_length": result.history_length,
        })
        await notifications.notify(
            "poc_step_complete",
            request.customer_id,
            detail=result.reply[:200],
        )

    async def on_error(exc):
        _fail_job(job_id, str(exc))

    session = _get_or_create_session(request.customer_id)
    asyncio.create_task(
        session.forge.run_turn_background(
            message=request.message,
            history=session.load_history(),
            context=session.context,
            on_complete=on_complete,
            on_error=on_error,
        )
    )
    return {"job_id": job_id, "status": "pending"}
```

Note: Adapt _get_or_create_session and session.load_history() to match whatever
pattern the existing /api/chat endpoint uses to load the session and history.
Do not change the session management pattern — mirror it exactly.

---

CHANGE 3: agent/notifications.py

Replace the TODO stub (lines 73-75) with a working Telegram call:

```python
async def _send_telegram(text: str) -> None:
    """Fire-and-forget Telegram message. Failure is silently swallowed."""
    import os
    try:
        import httpx
        cfg = _load_cfg()
        tg = cfg.get("telegram", {})
        if not tg.get("enabled", False):
            return
        token   = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        chat_id = os.environ.get(tg.get("chat_id_env", "TELEGRAM_CHAT_ID"), "")
        if not token or not chat_id:
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception:
        pass  # fire-and-forget — never propagate
```

Update the `_send` function to call `_send_telegram` and the `notify` function
to format a meaningful message:
  "✅ *{event}* for `{customer_id}`\n{detail}"

---

CHANGE 4: config.yaml

Add a telegram section (after the existing sub_agents section):

```yaml
telegram:
  enabled: false
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  chat_id_env: "TELEGRAM_CHAT_ID"
```

---

CHANGE 5: ui/src/components/ChatInterface.tsx

Add a "Background" mode toggle to the chat input area. When active:
- POST to /api/chat/background instead of opening SSE
- Show a dismissible pill: "Working in background — job {job_id}"
- Poll GET /api/job/{job_id} every 5 seconds
- When status == "complete": append reply to chat, clear pill
- When status == "error": show error toast, clear pill

---

Run ALL acceptance criteria:

  python3.11 -m py_compile skillforge/forge.py
  python3.11 -m py_compile drawing_agent_server.py
  python3.11 -m py_compile agent/notifications.py

  python3.11 -c "
  import asyncio, sys; sys.path.insert(0, '.')
  from skillforge.forge import Forge
  assert hasattr(Forge, 'run_turn_background'), 'FAIL: method missing'
  import inspect
  sig = inspect.signature(Forge.run_turn_background)
  assert 'on_complete' in sig.parameters, 'FAIL: on_complete param missing'
  assert 'on_error' in sig.parameters, 'FAIL: on_error param missing'
  print('PASS: run_turn_background method present with correct signature')
  "

  python3.11 -c "
  import yaml
  cfg = yaml.safe_load(open('config.yaml'))
  tg = cfg.get('telegram', {})
  assert 'enabled' in tg, 'FAIL: telegram.enabled missing'
  assert 'bot_token_env' in tg, 'FAIL: telegram.bot_token_env missing'
  print('PASS: telegram config present')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55b — POC Strategist: 3 parallel option exploration

```
Context: SEs need to go from rough requirements to a ranked list of POC options
scored on relevance, executability, cost-effectiveness, and security. Today
Archie has no capability to answer "what should we build?" This adds the
generate_poc_plan tool with 3 parallel sub-agent calls exploring different angles.

Pattern to follow exactly: sub_agents/pov/ and agent/tools/specialists.py
(_SpecialistHandler, TechResearchHandler). Copy the structure.

IMPORTANT: Branch from origin/main. (p55a can run in parallel.)

  git fetch origin
  git checkout -b claude/p55b origin/main

---

FILE 1: sub_agents/poc_strategist/__init__.py
(empty)

---

FILE 2: sub_agents/poc_strategist/config.yaml

```yaml
name: poc_strategist
port: 8089
llm:
  model_id: ""
  max_tokens: 2048
  temperature: 0.6
```

Note: 8089 avoids conflict with existing ports (8082-8087 in use).

---

FILE 3: sub_agents/poc_strategist/system_prompt.md

```markdown
# POC Strategist Sub-Agent

You are an Oracle Cloud Infrastructure POC strategy specialist.

Given customer context and an exploration angle, generate exactly ONE POC option
as a JSON object. The option must be specific to this customer — not a generic OCI demo.

## Scoring Dimensions

- **relevance_score** (1–10): Does this POC directly prove the customer's stated pain?
  A 9-10 means the demo shows exactly what the CFO/CTO asked about. A 5 means indirect.
- **executability_hours** (int): How many hours for an SE to build and demo this?
  Must be ≤ 8 for a viable POC. Include environment setup time.
- **cost_effectiveness** (string): Is the OCI monthly cost defensible vs. current spend?
  Quote estimated OCI cost range and comparison to what customer pays today (if known).
- **security_highlights** (list): Which OCI security controls would this customer care about?
  Use specific OCI service names: OCI Vault, ADB Dedicated VCN, Security Zones, etc.
- **wow_moment** (string): The single demo moment that lands hardest. One sentence.
- **demo_script_summary** (string): 2-3 sentence walk-through of what to show and in what order.
- **oci_services** (list): Exact OCI service names. No generic names like "database" — use
  "Autonomous Database Serverless" or "MySQL HeatWave".
- **option_name** (string): Specific to this customer. Not "Database POC" — use
  "Live Oracle DB → ADB-Dedicated migration for Acme Financial".

## Angles

migration_modernization: Focus on moving existing Oracle workloads to OCI native services.
  Best for customers with on-prem Oracle DB, WebLogic, or SOA. Emphasize Data Pump migration,
  APEX, ADB performance, and BYOL license mobility.

performance_scale_ai: Focus on OCI performance advantages and AI/ML capabilities.
  Best for customers with scale challenges or AI initiatives. Emphasize OCI GPU clusters,
  OKE, HeatWave ML, OCI AI services (Vision, Language, Generative AI).

cost_optimization_tco: Focus on total cost reduction vs. current spend.
  Best for customers with budget pressure. Emphasize OCI Universal Credits, OKE free
  control plane, egress pricing, Ampere A1 compute, Reserved Instances.

## Output Format

Return exactly this JSON (no markdown, no prose):

{
  "option_name": "string — customer-specific title",
  "angle": "migration_modernization | performance_scale_ai | cost_optimization_tco",
  "relevance_score": 8,
  "executability_hours": 4,
  "cost_effectiveness": "string — OCI cost range and comparison",
  "security_highlights": ["OCI Vault KMS", "ADB Dedicated VCN"],
  "oci_services": ["Autonomous Database Serverless", "OCI Compute E5.Flex"],
  "wow_moment": "string — one sentence",
  "demo_script_summary": "string — 2-3 sentences"
}

If customer context is insufficient, return:
{"type": "needs_input", "reply": "One sentence stating the specific missing input."}
```

---

FILE 4: sub_agents/poc_strategist/server.py

Copy sub_agents/pov/server.py exactly. Change:
- agent_name = "poc_strategist"
- AgentCard: name="poc_strategist", description="OCI POC strategy specialist...",
  inputs=["task", "angle", "customer_context"], required=["task"]
- Port from config.yaml (8089)
- Remove any prior_version / revision handling

---

CHANGE 5: agent/tools/specialists.py

Add at the bottom of the file (after TechResearchHandler):

```python
class PocStrategistHandler:
    """
    Explores 3 parallel POC options (migration, AI/ML, cost) and recommends one.
    Makes 3 concurrent sub-agent calls — NOT 1 call asking for 3 options.
    """
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        import asyncio, json as _json
        from agent.sub_agent_client import call_sub_agent

        dc = getattr(memory, "decision_context", {}) or {}
        pain = dc.get("pain_statement", "")
        platform = dc.get("current_platform", "")

        if not pain or not platform:
            return ToolResult(
                status="needs_input",
                summary="Need customer pain statement and current platform before planning POC options.",
                clarification=(
                    "What is the customer's primary pain (cost, performance, risk, compliance) "
                    "and what platform are they currently running on?"
                ),
            )

        user_message = args.get("_user_message", "")
        base_task = (
            f"Customer: {self._customer_name}\n"
            f"Pain: {pain}\n"
            f"Current platform: {platform}\n"
            f"Additional context: {user_message}\n\n"
            f"Decision context:\n{_json.dumps(dc, indent=2)}"
        )

        results = await asyncio.gather(
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "migration_modernization", "customer_id": self._customer_id},
                trace_id=trace_id),
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "performance_scale_ai", "customer_id": self._customer_id},
                trace_id=trace_id),
            call_sub_agent("poc_strategist",
                task=base_task,
                engagement_context={"angle": "cost_optimization_tco", "customer_id": self._customer_id},
                trace_id=trace_id),
            return_exceptions=True,
        )

        options = []
        for r in results:
            if isinstance(r, Exception):
                continue
            try:
                opt = _json.loads(r.result) if hasattr(r, "result") else {}
                if opt.get("option_name"):
                    options.append(opt)
            except Exception:
                pass

        if not options:
            return ToolResult(status="error", summary="All 3 POC exploration angles failed.")

        options.sort(
            key=lambda o: o.get("relevance_score", 0) / max(o.get("executability_hours", 8), 1),
            reverse=True,
        )
        rec = options[0]
        payload = {
            "poc_options": options,
            "recommendation": {
                "poc_name": rec.get("option_name"),
                "rationale": (
                    f"Highest relevance ({rec.get('relevance_score')}/10) "
                    f"with {rec.get('executability_hours')}h build time. "
                    f"Wow moment: {rec.get('wow_moment', '')}"
                ),
                "build_sequence": [],
                "success_criteria": rec.get("wow_moment", ""),
                "demo_script": rec.get("demo_script_summary", ""),
            }
        }

        key = f"poc_plan/{self._customer_id}/v1.json"
        await self._store.save_doc(key, _json.dumps(payload, indent=2))

        return ToolResult(
            status="ok",
            summary=f"Generated 3 POC options. Recommended: {rec.get('option_name')}",
            artifact_key=key,
            data=payload,
        )
```

---

FILE 6: agent/hats/oci_poc_strategist.md

Create following the exact format of agent/hats/oci_bom_expert.md.
Required sections: YAML frontmatter, intro, Core Principles, Quality Bar,
Output Contract, Critic Evaluation Guidance, Failure Questions, Activation & Drop,
Pre-Action Checklist, Post-Action Review.

Key YAML values:
```yaml
version: "1.0"
display_name: "OCI POC Strategist"
hat_rules:
  when_to_activate:
    - "user asks what to build for a customer"
    - "user asks for POC options, POC recommendation, or POC plan"
    - "user says 'what should we demo', 'what POC should we run', 'help me plan a POC'"
    - "SE has rough customer requirements and no POC direction yet"
  suggested_next_hat: "diagram_for_oci"
memory_focus:
  priority_fields:
    - pain_statement
    - current_platform
    - deal_stage
    - timeline
    - budget_signal
    - customer_industry
    - competitive_context
  summary_style: "poc_strategy_oriented"
  include_full_memory: false
coordination:
  parallel_with: ["infra_tech_research"]
  suggested_next_hat: "diagram_for_oci"
```

Pre-Action Checklist must include:
- If pain_statement absent → NEEDS_CLARIFICATION: "What is the customer's primary pain?"
- If current_platform absent → NEEDS_CLARIFICATION: "What platform is the customer on today?"
- Default deal_stage to "discovery" if absent
- Default timeline to "flexible" if absent

Quality Bar (10 items):
1. Exactly 3 options returned
2. Each option has all 8 scored fields
3. recommendation.rationale cites at least one specific customer input
4. option_name is customer-specific (contains customer name or specific workload)
5. executability_hours ≤ 8 for all options
6. oci_services lists ≥ 2 specific OCI service names (not generic)
7. demo_script_summary is 2-3 sentences (not 1 word, not a paragraph)
8. All 3 angles present: migration, performance/AI, cost
9. artifact_key present
10. No placeholder text in any field

---

CHANGE 7: agent/archie_wiring.py

Add import:
  from agent.tools.specialists import ..., PocStrategistHandler

After the generate_waf registration block, add:

```python
forge.register_tool(
    "generate_poc_plan",
    PocStrategistHandler(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
    ),
    description=(
        "Explore 3 parallel POC options (migration, AI/ML, cost) and recommend the one "
        "most likely to close this deal. Scores each option on relevance, build time, "
        "cost-effectiveness, and security. Returns a ranked list with demo scripts. "
        "Call when the SE needs to know WHAT to build before building it."
    ),
    args={"feedback": ArgSchema(
        description="Optional focus areas or constraints for the POC options.",
        type="string",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_poc_strategist",
)
```

In _TOOL_SEQUENCING_RULES, add after the existing tool rules:

```
POC workflow: When the SE needs to know what to build, call generate_poc_plan first.
Present the 3 options to the user. After the user confirms a specific option,
call generate_diagram + generate_bom + generate_jep + generate_terraform +
generate_presentation together (they fan out in parallel via the parallel dispatch path).
Do not generate artifacts before the user confirms a POC option.
```

---

CHANGE 8: config.yaml

In the sub_agents section, add:
  poc_strategist: "http://localhost:8089"

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/tools/specialists.py
  python3.11 -m py_compile agent/archie_wiring.py
  python3.11 -m py_compile sub_agents/poc_strategist/server.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'oci_poc_strategist' in hats, f'FAIL: hat not found. Got: {list(hats.keys())}'
  print('PASS: oci_poc_strategist hat discovered')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import PocStrategistHandler
  print('PASS: PocStrategistHandler importable')
  import inspect
  src = inspect.getsource(PocStrategistHandler.__call__)
  assert 'asyncio.gather' in src, 'FAIL: asyncio.gather not found — must use 3 parallel calls'
  assert 'migration_modernization' in src, 'FAIL: migration angle missing'
  assert 'performance_scale_ai' in src, 'FAIL: AI angle missing'
  assert 'cost_optimization_tco' in src, 'FAIL: cost angle missing'
  print('PASS: 3 parallel gather calls present')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  from agent.archie_wiring import build_forge
  forge = build_forge(store=MagicMock(), customer_id='test', customer_name='Test',
                      text_runner=MagicMock(), step3_planning=False)
  tools = list(forge._registry.names())
  assert 'generate_poc_plan' in tools, f'FAIL: tool not registered. Got: {tools}'
  print('PASS: generate_poc_plan registered')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55c — Parallel artifact fan-out after POC option confirmed

```
Context: After the SE says "go with option 1" (or similar), all 5 artifacts
should start simultaneously. The existing asyncio.gather() path in
skillforge/forge.py lines 745-786 already handles ToolResult(status="parallel",
parallel_tools=[...]). Zero forge.py changes needed.

Depends on: p55b (PocStrategistHandler must exist and generate_presentation
must be registered — p55d).

IMPORTANT: Branch from origin/main (or after p55b merges).

  git fetch origin
  git checkout -b claude/p55c origin/main

---

CHANGE 1: agent/tools/specialists.py

In PocStrategistHandler.__call__, add confirmation detection at the very top,
before the exploration path:

```python
import re as _re
from skillforge.types import ParallelToolCall

# Detect POC confirmation intent
confirmed = _detect_poc_confirmation(args.get("_user_message", "").lower(), memory)
if confirmed is not None:
    return _build_fanout_result(confirmed, self._customer_name, memory)
```

Add these module-level functions (outside the class):

```python
def _detect_poc_confirmation(user_message: str, memory) -> dict | None:
    """Returns the confirmed poc_option dict, or None if no confirmation detected."""
    patterns = [
        (_re.compile(r'\boption\s*1\b'), 0),
        (_re.compile(r'\boption\s*2\b'), 1),
        (_re.compile(r'\boption\s*3\b'), 2),
        (_re.compile(r'\b(go\s+with|proceed|confirm|let[\'']?s\s+do|use\s+the)\b'), 0),
    ]
    poc_options = []
    if memory and hasattr(memory, "decision_context"):
        poc_options = memory.decision_context.get("poc_options", [])
    if not poc_options:
        return None
    for pattern, idx in patterns:
        if pattern.search(user_message):
            return poc_options[min(idx, len(poc_options) - 1)]
    return None


def _build_fanout_result(option: dict, customer_name: str, memory) -> ToolResult:
    """Build the parallel fan-out ToolResult for all 5 artifacts."""
    poc_name = option.get("option_name", "OCI POC")
    services = option.get("oci_services", [])
    dc = getattr(memory, "decision_context", {}) or {}
    slug = poc_name.lower().replace(" ", "-")[:40]

    return ToolResult(
        status="parallel",
        summary=f"POC confirmed: {poc_name}. Generating all artifacts in parallel...",
        parallel_tools=[
            ParallelToolCall(
                tool="generate_diagram",
                args={"_user_message": f"Create OCI architecture diagram for POC: {poc_name}. Services: {', '.join(services)}"},
            ),
            ParallelToolCall(
                tool="generate_bom",
                args={"_user_message": f"Generate BOM for POC: {poc_name}. Services: {', '.join(services)}. Region: {dc.get('region', 'us-chicago-1')}"},
            ),
            ParallelToolCall(
                tool="generate_jep",
                args={"_user_message": f"Create JEP execution plan for POC: {poc_name}. Build sequence: {option.get('build_sequence', [])}"},
            ),
            ParallelToolCall(
                tool="generate_terraform",
                args={"_user_message": f"Generate Terraform for POC: {poc_name}. Services: {', '.join(services)}"},
            ),
            ParallelToolCall(
                tool="generate_presentation",
                args={"_user_message": f"Create client PowerPoint deck for POC: {poc_name}", "poc_option": option},
            ),
        ],
    )
```

---

CHANGE 2: agent/archie_wiring.py

No registration change needed. The existing forge parallel dispatch handles it.
Only add generate_presentation to the system prompt _TOOL_SEQUENCING_RULES
so the sequencing hint mentions it (if not already done in p55d).

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/tools/specialists.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import _detect_poc_confirmation, _build_fanout_result
  from skillforge.types import ToolResult

  class FakeMemory:
      decision_context = {'poc_options': [
          {'option_name': 'DB Migration', 'oci_services': ['ADB']},
          {'option_name': 'OKE AI', 'oci_services': ['OKE', 'Data Science']},
          {'option_name': 'Cost Opt', 'oci_services': ['Compute']},
      ]}

  m = FakeMemory()

  # Test option selection by number
  assert _detect_poc_confirmation('go with option 2', m)['option_name'] == 'OKE AI', 'FAIL: option 2'
  assert _detect_poc_confirmation('option 3', m)['option_name'] == 'Cost Opt', 'FAIL: option 3'
  assert _detect_poc_confirmation('option 1', m)['option_name'] == 'DB Migration', 'FAIL: option 1'

  # Test ambiguous confirmation defaults to index 0
  assert _detect_poc_confirmation(\"let's do it\", m)['option_name'] == 'DB Migration', 'FAIL: default'
  assert _detect_poc_confirmation('proceed', m)['option_name'] == 'DB Migration', 'FAIL: proceed'

  # Test no match returns None
  assert _detect_poc_confirmation('what are the options', m) is None, 'FAIL: no match'

  print('PASS: confirmation detection correct')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import _build_fanout_result
  from skillforge.types import ToolResult

  class FakeMemory:
      decision_context = {}

  result = _build_fanout_result({'option_name': 'DB POC', 'oci_services': ['ADB']}, 'Acme', FakeMemory())
  assert result.status == 'parallel', f'FAIL: status={result.status}'
  tool_names = [pt.tool for pt in result.parallel_tools]
  expected = {'generate_diagram', 'generate_bom', 'generate_jep', 'generate_terraform', 'generate_presentation'}
  assert set(tool_names) == expected, f'FAIL: got {tool_names}'
  print('PASS: fan-out returns 5 parallel tools with correct names')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55d — PowerPoint presentation generation

```
Context: Zero PPTX capability exists today (confirmed: no python-pptx in
requirements.txt, no sub_agents/presentation/, no generate_presentation tool).

Reference: https://github.com/aruanurag/oci-architecture-codex-skill
Uses oracle-oci-architecture-toolkit-v24.1.pptx as master stencil — OCI service
icons are vector groups in that PPTX. python-pptx copies them into the output.
Same pattern as OCI_Library.xml for draw.io. Do NOT build shapes from scratch.

IMPORTANT: Branch from origin/main. (Independent of p55a/b/c.)

  git fetch origin
  git checkout -b claude/p55d origin/main

---

CHANGE 1: requirements.txt

Add if not present:
  python-pptx>=1.0.2

---

FILE 2: sub_agents/presentation/__init__.py
(empty)

---

FILE 3: sub_agents/presentation/config.yaml

```yaml
name: presentation
port: 8090
llm:
  model_id: ""
  max_tokens: 2048
  temperature: 0.2
```

---

FILE 4: sub_agents/presentation/system_prompt.md

```markdown
# Presentation Sub-Agent

You are an Oracle OCI PowerPoint architect.

Given a POC context, produce a 7-slide client-facing deck specification as JSON.
The spec is rendered by render_oci_powerpoint.py using Oracle's official icon stencils.

## Slide Structure (always exactly 7 slides)

1. title       — POC name + customer name + date
2. challenge   — customer pain statement, current state (3-4 bullets)
3. architecture — OCI service icons, VCN/subnet layout description
4. services    — key OCI services with one-liner per service
5. cost        — BOM summary table (service, qty, monthly cost)
6. timeline    — JEP phases as ordered list
7. next_steps  — success criteria + call to action

## Icon Names

Use exact Oracle toolkit icon names:
  "Autonomous Database Serverless" → OCI_Autonomous_Database
  "OCI Compute" → OCI_Compute_Instance
  "Virtual Cloud Network" → OCI_VCN
  "OKE" → OCI_Container_Engine_for_Kubernetes
  "Load Balancer" → OCI_Load_Balancer
  "Object Storage" → OCI_Object_Storage_Bucket
  "OCI Vault" → OCI_Key_Management

## Output Format

Return exactly this JSON (no markdown wrapper):

{
  "slides": [
    {"slide_number": 1, "layout": "title", "title": "string", "subtitle": "string"},
    {"slide_number": 2, "layout": "bullets", "title": "string", "bullets": ["string"]},
    {"slide_number": 3, "layout": "architecture", "title": "string", "oci_services": [{"name": "string", "icon": "string"}], "topology_description": "string"},
    {"slide_number": 4, "layout": "services", "title": "string", "services": [{"name": "string", "description": "string"}]},
    {"slide_number": 5, "layout": "table", "title": "string", "rows": [{"service": "string", "qty": "string", "monthly_cost": "string"}], "total": "string"},
    {"slide_number": 6, "layout": "timeline", "title": "string", "phases": ["string"]},
    {"slide_number": 7, "layout": "next_steps", "title": "string", "bullets": ["string"], "cta": "string"}
  ]
}
```

---

FILE 5: sub_agents/presentation/scripts/resolve_oci_powerpoint_icon.py

```python
"""Maps OCI service display names to shape names in oracle-oci-architecture-toolkit-v24.1.pptx."""

OCI_ICON_MAP = {
    "Autonomous Database Serverless": "OCI_Autonomous_Database",
    "Autonomous Database": "OCI_Autonomous_Database",
    "ADB": "OCI_Autonomous_Database",
    "OCI Compute": "OCI_Compute_Instance",
    "Compute": "OCI_Compute_Instance",
    "Virtual Cloud Network": "OCI_VCN",
    "VCN": "OCI_VCN",
    "OKE": "OCI_Container_Engine_for_Kubernetes",
    "Container Engine for Kubernetes": "OCI_Container_Engine_for_Kubernetes",
    "Load Balancer": "OCI_Load_Balancer",
    "Object Storage": "OCI_Object_Storage_Bucket",
    "OCI Vault": "OCI_Key_Management",
    "Vault": "OCI_Key_Management",
    "MySQL HeatWave": "OCI_MySQL_HeatWave",
    "Data Science": "OCI_Data_Science",
    "OCI AI Services": "OCI_AI_Services",
    "Generative AI": "OCI_Generative_AI",
}


def resolve_icon(service_name: str) -> str | None:
    return OCI_ICON_MAP.get(service_name) or OCI_ICON_MAP.get(service_name.strip())
```

---

FILE 6: sub_agents/presentation/scripts/render_oci_powerpoint.py

```python
"""
Renders a 7-slide OCI POC deck from a JSON spec using python-pptx.
Uses oracle-oci-architecture-toolkit-v24.1.pptx for OCI icon stencils.
"""
from __future__ import annotations
import io
from copy import deepcopy
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor

TOOLKIT_PATH = Path(__file__).parent.parent / "assets" / "oracle-oci-architecture-toolkit-v24.1.pptx"

ORACLE_RED   = RGBColor(0xC7, 0x46, 0x34)
ORACLE_DARK  = RGBColor(0x1A, 0x1A, 0x1A)
ORACLE_WHITE = RGBColor(0xFF, 0xFF, 0xFF)


def render(spec: dict[str, Any], output_path: str) -> None:
    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    for slide_spec in spec.get("slides", []):
        slide = prs.slides.add_slide(blank)
        layout = slide_spec.get("layout", "bullets")
        title  = slide_spec.get("title", "")

        _set_background(slide, layout)
        _add_title(slide, title, layout)

        if layout == "title":
            subtitle = slide_spec.get("subtitle", "")
            _add_textbox(slide, subtitle, Inches(1.5), Inches(4.0), Inches(10), Inches(1.2),
                         size=24, color=ORACLE_WHITE)
        elif layout == "architecture":
            services = slide_spec.get("oci_services", [])
            desc = slide_spec.get("topology_description", "")
            _add_architecture_slide(slide, services, desc)
        elif layout == "table":
            rows  = slide_spec.get("rows", [])
            total = slide_spec.get("total", "")
            _add_table_slide(slide, rows, total)
        elif layout == "timeline":
            phases = slide_spec.get("phases", [])
            _add_bullets(slide, phases, Inches(0.5), Inches(1.8), Inches(12.3), Inches(5.0))
        elif layout in ("bullets", "services", "next_steps"):
            items = slide_spec.get("bullets") or [
                f"{s['name']}: {s['description']}" for s in slide_spec.get("services", [])
            ]
            _add_bullets(slide, items, Inches(0.5), Inches(1.8), Inches(12.3), Inches(5.0))
            if layout == "next_steps":
                cta = slide_spec.get("cta", "")
                if cta:
                    _add_textbox(slide, cta, Inches(0.5), Inches(6.5), Inches(12.3), Inches(0.7),
                                 size=16, color=ORACLE_RED, bold=True)

    prs.save(output_path)


def _set_background(slide, layout: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = ORACLE_DARK if layout == "title" else ORACLE_WHITE


def _add_title(slide, text: str, layout: str) -> None:
    color = ORACLE_WHITE if layout == "title" else ORACLE_DARK
    _add_textbox(slide, text, Inches(0.5), Inches(0.3), Inches(12.3), Inches(1.2),
                 size=32 if layout == "title" else 28, color=color, bold=True)


def _add_textbox(slide, text, left, top, width, height, size=18, color=None, bold=False):
    if color is None:
        color = ORACLE_DARK
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size  = Pt(size)
    p.font.bold  = bold
    p.font.color.rgb = color


def _add_bullets(slide, items, left, top, width, height):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"• {item}"
        p.font.size = Pt(18)
        p.font.color.rgb = ORACLE_DARK


def _add_architecture_slide(slide, services, description):
    from sub_agents.presentation.scripts.resolve_oci_powerpoint_icon import resolve_icon
    y = Inches(1.8)
    for svc in services[:6]:
        icon_name = resolve_icon(svc.get("name", ""))
        copied = _copy_icon(icon_name, slide) if icon_name else False
        label = svc.get("name", "")
        _add_textbox(slide, label, Inches(0.5), y, Inches(2.5), Inches(0.4), size=14)
        y += Inches(0.8)
    if description:
        _add_textbox(slide, description, Inches(6.5), Inches(1.8), Inches(6.3), Inches(4.5), size=14)


def _copy_icon(shape_name: str, target_slide) -> bool:
    """Copy a named shape from the Oracle toolkit PPTX into target_slide."""
    if not TOOLKIT_PATH.exists():
        return False
    try:
        toolkit = Presentation(str(TOOLKIT_PATH))
        for slide in toolkit.slides:
            for shape in slide.shapes:
                if shape.name == shape_name:
                    sp = deepcopy(shape._element)
                    target_slide.shapes._spTree.append(sp)
                    return True
    except Exception:
        pass
    return False


def _add_table_slide(slide, rows, total):
    y = Inches(1.8)
    for row in rows:
        line = f"  {row.get('service',''):<35} {row.get('qty',''):<10} {row.get('monthly_cost','')}"
        _add_textbox(slide, line, Inches(0.5), y, Inches(12.3), Inches(0.4), size=14)
        y += Inches(0.4)
    if total:
        _add_textbox(slide, f"Monthly Total: {total}", Inches(0.5), y + Inches(0.2),
                     Inches(6), Inches(0.5), size=18, bold=True, color=ORACLE_RED)
```

---

FILE 7: sub_agents/presentation/server.py

Copy sub_agents/pov/server.py. Change:
- agent_name = "presentation"
- AgentCard inputs = ["task", "poc_name", "customer_name", "oci_services", "bom_summary", "jep_phases"]
- required = ["task", "customer_name"]
- Port 8090 from config.yaml
- After LLM returns JSON spec, call render_oci_powerpoint.render(spec, tmp_path),
  read bytes, encode base64, include in response:
  {"result": <base64_pptx>, "status": "ok"}

---

FILE 8: agent/hats/oci_presentation_writer.md

Lightweight hat following oci_bom_expert.md format. Key values:

```yaml
version: "1.0"
display_name: "OCI Presentation Writer"
memory_focus:
  priority_fields: [poc_recommendation, customer_name, bom_summary, jep_phases, pain_statement]
coordination:
  parallel_with: ["generate_diagram", "generate_bom", "generate_jep", "generate_terraform"]
```

Pre-Action Checklist:
- poc_recommendation absent → NEEDS_CLARIFICATION: "No POC has been planned yet. Run generate_poc_plan first."
- customer_name absent → NEEDS_CLARIFICATION: "What is the customer's name?"

Quality Bar:
1. Exactly 7 slides returned
2. Customer name on title slide
3. Oracle OCI icon names used (OCI_Autonomous_Database not "database")
4. BOM numbers present if bom_summary in memory
5. artifact_key present and ends in .pptx

---

FILE 9: agent/tools/presentation.py

```python
"""Forge tool handler for generate_presentation."""
import base64
import json as _json

from agent import sub_agent_client
from skillforge.types import ToolResult


class PresentationHandler:
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        dc = getattr(memory, "decision_context", {}) or {}
        poc_option = args.get("poc_option") or dc.get("poc_recommendation", {})

        payload = {
            "poc_name":       poc_option.get("option_name", "OCI POC"),
            "customer_name":  self._customer_name,
            "pain_statement": dc.get("pain_statement", ""),
            "oci_services":   poc_option.get("oci_services", []),
            "bom_summary":    dc.get("bom_summary", ""),
            "jep_phases":     dc.get("jep_phases", []),
        }

        response = await sub_agent_client.call_sub_agent(
            "presentation",
            task=f"Generate POC deck for {self._customer_name}: {payload['poc_name']}",
            engagement_context=payload,
            trace_id=trace_id,
        )

        if response.status != "ok":
            return ToolResult(status="error", summary=f"Presentation failed: {response.result}")

        pptx_bytes = base64.b64decode(response.result)
        key = f"presentation/{self._customer_id}/v1.pptx"
        await self._store.save_doc(key, pptx_bytes)

        return ToolResult(
            status="ok",
            summary=f"PowerPoint deck generated: {payload['poc_name']} ({len(pptx_bytes):,} bytes)",
            artifact_key=key,
        )
```

---

CHANGE 10: agent/archie_wiring.py

Add import: from agent.tools.presentation import PresentationHandler

Register after generate_sales_deck (or generate_waf if sales_deck not present):

```python
forge.register_tool(
    "generate_presentation",
    PresentationHandler(store=store, customer_id=customer_id, customer_name=customer_name),
    description=(
        "Generate a 7-slide Oracle-standard PowerPoint POC deck with OCI icon stencils. "
        "Produces a downloadable .pptx. Call after generate_poc_plan confirms the POC option."
    ),
    args={"poc_option": ArgSchema(
        description="POC option dict from generate_poc_plan (injected automatically in fan-out).",
        type="object",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_presentation_writer",
)
```

---

CHANGE 11: drawing_agent_server.py

In the /download endpoint, add PPTX content-type branch before the default return:

```python
if artifact_key.endswith(".pptx"):
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{artifact_key.split(\"/\")[-1]}"'},
    )
```

---

CHANGE 12: config.yaml

In sub_agents section, add:
  presentation: "http://localhost:8090"

---

Run ALL acceptance criteria:

  pip install python-pptx --quiet
  python3.11 -m py_compile agent/tools/presentation.py
  python3.11 -m py_compile sub_agents/presentation/server.py
  python3.11 -m py_compile sub_agents/presentation/scripts/render_oci_powerpoint.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'oci_presentation_writer' in hats, f'FAIL: hat not found. Got: {list(hats.keys())}'
  print('PASS: oci_presentation_writer hat discovered')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from sub_agents.presentation.scripts.render_oci_powerpoint import render
  import tempfile, os, zipfile
  spec = {'slides': [
      {'slide_number': 1, 'layout': 'title', 'title': 'OCI POC for Acme', 'subtitle': 'Solutions Review'},
      {'slide_number': 2, 'layout': 'bullets', 'title': 'Customer needs cost reduction', 'bullets': ['Bullet 1', 'Bullet 2']},
      {'slide_number': 3, 'layout': 'architecture', 'title': 'OCI Architecture', 'oci_services': [{'name': 'ADB', 'icon': 'OCI_Autonomous_Database'}], 'topology_description': 'Single AD, us-chicago-1'},
      {'slide_number': 4, 'layout': 'services', 'title': 'Key OCI Services', 'services': [{'name': 'ADB', 'description': 'Managed Oracle Database'}]},
      {'slide_number': 5, 'layout': 'table', 'title': 'Cost Estimate', 'rows': [{'service': 'ADB', 'qty': '1 ECPU', 'monthly_cost': '\$200'}], 'total': '\$200/mo'},
      {'slide_number': 6, 'layout': 'timeline', 'title': 'Implementation Plan', 'phases': ['Phase 1: Provision ADB', 'Phase 2: Migrate data']},
      {'slide_number': 7, 'layout': 'next_steps', 'title': 'Next Steps', 'bullets': ['Schedule POC kickoff'], 'cta': 'Start POC by June 1'},
  ]}
  with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as f:
      tmp = f.name
  render(spec, tmp)
  assert os.path.exists(tmp) and os.path.getsize(tmp) > 1000, 'FAIL: pptx not created'
  assert zipfile.is_zipfile(tmp), 'FAIL: not valid pptx'
  from pptx import Presentation as P
  prs = P(tmp)
  assert len(prs.slides) == 7, f'FAIL: expected 7 slides, got {len(prs.slides)}'
  os.unlink(tmp)
  print('PASS: render produces valid 7-slide PPTX')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  from agent.archie_wiring import build_forge
  forge = build_forge(store=MagicMock(), customer_id='test', customer_name='Test',
                      text_runner=MagicMock(), step3_planning=False)
  tools = list(forge._registry.names())
  assert 'generate_presentation' in tools, f'FAIL: not registered. Got: {tools}'
  print('PASS: generate_presentation registered')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Run Order

```
p55a (background jobs)  ─┐
p55b (poc strategist)   ─┤── all independent, run in parallel
p55d (powerpoint)       ─┘
p55c (fan-out)          ── depends on p55b + p55d (needs both tools registered)
```

## Critical Files

| File | Task | Change type |
|---|---|---|
| `skillforge/forge.py` | p55a | Add `run_turn_background()` |
| `drawing_agent_server.py` | p55a | New `/api/chat/background` endpoint |
| `agent/notifications.py` | p55a | Implement Telegram call (replace TODO stub) |
| `config.yaml` | p55a, p55b, p55d | Add telegram section + sub-agent URLs |
| `ui/src/components/ChatInterface.tsx` | p55a | Background mode toggle + poll |
| `sub_agents/poc_strategist/` | p55b | New sub-agent (4 files) |
| `agent/tools/specialists.py` | p55b, p55c | PocStrategistHandler + confirmation/fan-out |
| `agent/hats/oci_poc_strategist.md` | p55b | New hat |
| `agent/archie_wiring.py` | p55b, p55d | Register 2 new tools + update sequencing |
| `sub_agents/presentation/` | p55d | New sub-agent (5 files + assets/) |
| `agent/tools/presentation.py` | p55d | New handler |
| `agent/hats/oci_presentation_writer.md` | p55d | New hat |
| `requirements.txt` | p55d | Add python-pptx |

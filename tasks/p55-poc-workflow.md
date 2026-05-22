# p55 — POC Workflow: Strategy, Background Execution, Parallel Artifacts, PowerPoint

**Revision 2 — Vision-aligned rewrite**

---

## Vision Alignment

Archie should feel like a **proactive senior OCI Solutions Architect partner** — the SE's most
experienced colleague who has run hundreds of OCI POCs, knows which demos close deals
by industry and deal stage, anticipates risks before they surface, and gives opinionated
recommendations backed by specific evidence.

**The mental model:**
- **Hats are thinking lenses.** When Archie wears the POC Strategist hat, it reasons as
  a battle-hardened field SE who has seen every flavor of "customer wants to evaluate OCI."
  The hat's pre-action is where the expertise lives — pattern recognition, deal-stage
  sensitivity, risk anticipation, success-pattern matching.
- **Sub-agents are execution specialists.** They do the work the hat prescribes.
  The hat tells the sub-agent exactly what to produce and why; the sub-agent executes.
- **Prompt-first.** Business logic that involves semantic judgment (when to confirm,
  how to present options, what to say in the chat) belongs in the system prompt and
  hats — not in Python pattern-matching.

---

## Gap Analysis (revised)

| Capability | Status | Root cause of gap |
|---|---|---|
| "What should we build?" | Missing | No POC archetype reasoning — only artifact generation |
| Expert POC risk reasoning | Missing | `infra_tech_research` covers service selection, not deal strategy |
| Background execution | Missing | SSE requires open connection for full turn |
| Parallel artifact generation | Blocked by B | Fan-out path exists in Forge — needs triggering |
| PowerPoint as synthesis | Missing | No PPTX capability; no artifact-content synthesis |
| Telegram notification | Stub only | `notifications.py` has TODO, never implemented |
| Natural background UX | Missing | 202 + job_id is not a chat response |

---

## What Changes (Forge vs. Archie boundary)

| Layer | Change | Why |
|---|---|---|
| **Forge** (`skillforge/`) | `run_turn_background()` — one new method | Forge owns turn execution mechanics |
| **Archie** (`agent/`) | POC Strategist hat, Presentation hat, system prompt additions | All OCI/SE domain content belongs here |
| **Archie** (`sub_agents/`) | `poc_strategist`, `presentation` sub-agents | Execution specialists |
| **Server** (`drawing_agent_server.py`) | `/api/chat/background` + acknowledgment | Infrastructure serving Archie |

Forge gets exactly one new method. The POC strategy logic, deal archetypes, and
artifact synthesis all live in hats and prompts.

---

## Success Criteria

A successful p55 means:

1. **SE can say:** "Customer is a financial services firm, Oracle RAC on-prem, CFO flagged
   $2M bill, exec review in 3 weeks" — and Archie responds with 3 scored POC options,
   recommends the ADB migration with rationale citing "$2M" and "3 weeks" specifically,
   includes the wow moment and known risks for that customer profile.

2. **SE can say:** "Running to a meeting, kick this off" — Archie responds instantly with
   "On it — exploring 3 POC angles for Acme Financial. Takes ~2 minutes. I'll send you a
   Telegram when the plan is ready." SE receives a Telegram message with the top option
   and a call-to-action.

3. **SE can say:** "Go with the DB migration" — Archie triggers 5 artifacts simultaneously.
   All 5 are ready within 90 seconds. No code ran pattern-matching on "DB migration" — the
   LLM recognized the confirmation intent and called `generate_poc_plan(action="confirm")`.

4. **SE downloads the PowerPoint.** Slide 5 shows the actual BOM numbers from the generated
   BOM artifact. Slide 3 describes the actual topology from the diagram artifact. The executive
   summary on slide 2 uses language from the POV artifact if one exists. The deck tells a
   coherent customer story — not a generic OCI template.

---

## p55 Task Overview

| Task | Description | Layer | Effort | Depends on |
|---|---|---|---|---|
| **p55a** | Background job for chat turns + Telegram | Forge + Server | 1 day | — |
| **p55b** | POC Strategist hat — expert-level thinking lens | Archie/Hats | 1 day | — |
| **p55c** | POC Strategist tool — sub-agent + handler | Archie/Tools | 1 day | p55b |
| **p55d** | Archie system prompt: POC workflow + confirm mode | Archie/Prompt | 0.5 days | p55b |
| **p55e** | Presentation — hat + synthesis handler + sub-agent + renderer | Archie | 1.5 days | — |

p55a, p55b, p55e are independent — run in parallel.
p55c requires p55b (hat defines what the sub-agent must produce).
p55d requires p55b and p55c (prompt references both tool and hat behavior).

---

## POC Strategist Hat — Expert Lens Specification

This is the most important artifact in p55. The hat is what makes Archie feel like
a senior SE — not the Python handler.

### What the hat must encode

**POC archetypes with success patterns** (the hat's Core Principles should name these
explicitly so pre-action reasoning can pattern-match):

| Archetype | Best for | Typical build time | Wow moment | Win rate signal |
|---|---|---|---|---|
| Oracle DB → ADB Migration | Oracle on-prem with cost pain | 4h | Customer's own query on ADB vs. RAC, cost delta live | High — direct pain proof |
| OKE Modernization | K8s on-prem or AWS, DevOps teams | 6–8h | Deployment in minutes, show HPA under load | Medium — developer audience |
| HeatWave Acceleration | MySQL + analytics, BI teams | 3h | Same MySQL query, 10–100× faster with ML | High — immediate visible result |
| AI/ML on OCI | New AI initiative, GPU cost pressure | 8h+ | Model serving latency vs. cost vs. AWS | Medium — needs ML context |
| Cost Optimization + Reserved | CFO-driven, multi-cloud | 2h | Live OCI pricing calculator vs. current AWS/Azure bill | Medium — harder to make concrete |
| Disaster Recovery on OCI | Compliance-driven, risk concerns | 4h | Cross-region failover in under 15 minutes | High — risk audience |

**Industry-specific POC overlay** (the hat pre-action should apply these):

- **Financial Services:** Lead with security + compliance (OCI Security Zones, Data Safe,
  encryption in transit/at rest, network isolation). Cost is secondary. Auditors will ask.
- **Healthcare:** HIPAA posture first (OCI BAA available), autonomous backup, data residency.
  Show the compliance controls before showing the database.
- **Retail:** Show scale simulation. OKE + HeatWave for personalization recommendations.
  Black Friday load scenario resonates.
- **Manufacturing:** OCI FastConnect / Site-to-Site VPN for plant connectivity.
  Real-time streaming from shop floor to OCI analytics.
- **Public Sector:** FedRAMP/IL4 if applicable. OCI Dedicated Region for air-gap scenarios.

**Deal stage sensitivity** (the hat pre-action must apply this heuristic):

- **Discovery:** Offer 3 options, let them choose the angle that resonates.
- **Evaluation (current stage):** One focused POC, maximum relevance to stated pain.
  Do not offer alternatives — recommend and justify.
- **Decision:** Risk-reduction POC. Show migration path, TCO, and what happens if it fails.
  Customer needs confidence, not new features.
- **POC in progress:** Support, don't pivot. The hat should surface "already in POC" as
  a memory conflict if applicable.

**What kills POCs** (the hat's risk register must check for these):

- No agreed success criteria before the demo starts
- Wrong audience (DB performance demo for business stakeholders)
- Wow moment buried — happens in step 15, audience attention gone by step 8
- Build time underestimated — SE scrambles during the meeting
- Competitor FUD not pre-addressed ("AWS already does this")
- Pre-provisioning skipped — cluster/DB not ready when customer joins
- Region availability not confirmed (GPU shapes, ADB-D in specific regions)

### Hat pre-action structure (for Task p55b)

The pre-action for `generate_poc_plan` must follow the Forge Step 4 format exactly
(KNOWN FACTS → GAPS → EXPERT ASSESSMENT → SUB-AGENT TASK) but with POC-specific content
in the EXPERT ASSESSMENT section:

```
EXPERT ASSESSMENT (POC STRATEGY):

CUSTOMER ARCHETYPE: [Match to the archetype table above. Name it explicitly.]

DEAL STAGE READING: [Discovery / Evaluation / Decision. What evidence from the
conversation or memory points to this stage? How does it affect option selection?]

POC SELECTION REASONING:
- [Angle 1 name] — [Why strong or weak for THIS customer. Reference specific memory
  facts: pain statement, platform, timeline, budget signal. Score 1-10.]
- [Angle 2 name] — [Same treatment]
- [Angle 3 name] — [Same treatment]
- RECOMMENDED: [Angle X] because [cite 2-3 specific facts from KNOWN FACTS]

SUCCESS PATTERN for [recommended archetype]:
- Step 1: [Specific first demo action — usually "provision X in Y minutes" to show OCI speed]
- Step 2: [Core proof step — the action that directly proves the customer's pain]
- Step 3: [Cost/compliance moment]
- Step 4: [Call to action setup]

WOW MOMENT: [The single 30-second demo moment that closes the conversation.
Must be specific: "run customer's own query" not "show performance."]

TOP POC RISKS:
- Risk 1: [Specific risk that has killed similar POCs] — Mitigation: [concrete action]
- Risk 2: [Second risk] — Mitigation: [concrete action]

COMPETITIVE CONTEXT: [If competitor is in play, name the OCI-specific differentiator
that this POC proves. E.g., "AWS RDS Oracle is shared hardware; ADB-D is real Exadata."]

PROACTIVE FLAG: [One thing the SE should do BEFORE the demo that the customer hasn't asked.
E.g., "Run Oracle DB Compatibility Checker before scoping — stored procedures are the
silent killer of migration POCs."]

SUB-AGENT TASK:
[Exact instructions for the poc_strategist sub-agent for THIS angle. Include:
customer archetype, industry context, deal stage, recommended POC, success pattern,
wow moment, top risks, any pre-demo preparation required.]
```

### Hat Quality Bar (for Task p55b)

A POC plan that passes the quality bar must have:

1. Customer archetype named and matched to the archetype table
2. Deal stage identified with evidence from memory
3. Recommended option cites ≥2 specific facts from KNOWN FACTS (not generic)
4. WOW MOMENT is a concrete 30-second action (not "show performance")
5. ≥2 risks named with specific mitigations (not "things might go wrong")
6. Competitive context addressed if competitor mentioned in memory
7. `artifact_key` present (plan saved to document store)
8. Option names are customer-specific: "Oracle RAC → ADB-D migration for Acme Financial"
   not "Database POC"

---

## Prompt-First Approach: POC Confirmation + Fan-out

**The problem with regex:** Python pattern matching on "option 1" or "go with it" is
fragile and wrong-layer. The LLM should understand confirmation semantically.

**The solution:** `generate_poc_plan` has two modes, controlled by an `action` argument.

- `action="explore"` (default): run the 3 parallel sub-agent evaluations, return options
- `action="confirm"` + `confirmed_option_name`: look up the confirmed option from memory,
  return `ToolResult(status="parallel", parallel_tools=[...])` with all 5 artifacts

Archie decides when to call `action="confirm"` based on system prompt instructions.
The LLM extracts the option name semantically — even if the user says "let's do the
migration one" or "number 2."

**System prompt addition to `_TOOL_SEQUENCING_RULES`** (for Task p55d):

```
### POC Planning Workflow

When the SE needs to know what to build for a customer:
1. Call generate_poc_plan (default, action="explore"). This explores 3 POC angles and
   returns ranked options with demo scripts.
2. Present the options clearly. For each: name, relevance score, build time, wow moment.
   Give a recommendation with rationale citing 2-3 specific customer facts.
   End with: "Which option would you like to proceed with?"
3. When the user confirms a specific option — by number ("option 1"), by name ("the DB
   migration"), by description ("the cost optimization one"), or by affirmation ("that
   one", "go with it", "yes") — extract the confirmed_option_name from the options list
   and call generate_poc_plan with action="confirm" and confirmed_option_name.
4. The confirm call fans out all 5 artifacts simultaneously: diagram, BOM, JEP, Terraform,
   and presentation. When they complete, present the full POC kit coherently.
5. Do NOT call generate_poc_plan with action="confirm" until the user explicitly selects.
   If ambiguous, ask: "Which option — the [name1] or the [name2]?"

Note: The confirm call bypasses the 3-angle exploration. If the user later says "actually
try option 2 instead", call generate_poc_plan(action="confirm", confirmed_option_name=option2).
```

---

## Background Job UX Specification

**The problem with raw 202 + job_id:** An SE getting a bare HTTP response is not a
chat experience. The response must feel like Archie replied.

**What "seamless in chat" means:**

1. **Immediate acknowledgment.** When the user triggers background mode, Archie replies
   instantly (before the background job starts) with a natural language message:

   > "On it — starting POC strategy analysis for [customer_name] in the background.
   > I'll explore DB migration, AI/ML, and cost optimization angles in parallel.
   > Usually takes 2–3 minutes. I'll notify you on Telegram when it's ready,
   > or you can ask me anything else in the meantime. Job: `{job_id}`"

   This acknowledgment is pre-generated (templated from the turn context) and returned
   as part of the 202 response alongside the job_id.

2. **Telegram notification includes actionable content:**

   > "✅ *Archie: POC plan ready* for Acme Financial
   > Recommended: Oracle DB → ADB Migration (9/10 relevance, 4h build)
   > Wow moment: Run Acme's own query — show 2× performance + 40% cost delta live
   > 3 options explored. Reply 'confirm option 1' in chat to generate artifacts."

   Not: "Your job is complete."

3. **When the user returns to chat**, the completed result is appended naturally.
   The chat history should read as if Archie was working while the SE was away.

4. **Easy follow-up**: the acknowledgment message includes the job_id so the SE can
   reference it if they want to check status.

**Implementation note**: The `/api/chat/background` endpoint should generate the
acknowledgment synchronously (template-based, not an LLM call) and include it in
the 202 response. The UI appends it to the chat thread immediately on 202 receipt.

---

## Presentation as Synthesis Specification

**The problem with reference-only:** Passing artifact keys to the presentation sub-agent
produces a template deck. Passing actual artifact content produces a story.

**What synthesis means:**

The `PresentationHandler` must load actual content before calling the sub-agent:

1. **BOM artifact** → load from document_store → extract: monthly_total, top 5 services,
   key assumptions. Use these for Slide 5 (cost estimate) — real numbers, not "TBD."

2. **Diagram artifact** → the artifact_key is a `.drawio` file. Extract the service names
   from the XML (or use the BOM's oci_services_required). Use for Slide 3 description.

3. **Research artifact** → load from document_store → extract: recommendation rationale,
   risk_register top risks, competitive differentiators. Use for Slide 6 (why OCI) and
   Slide 7 (next steps risk mitigation).

4. **POV artifact** → if exists, load executive_summary. Use verbatim (or lightly edited)
   for Slide 2 (customer challenge) — the POV writer already crafted the customer narrative.

5. **POC recommendation** → from memory.decision_context.poc_recommendation → use
   poc_name for title, wow_moment for Slide 7 CTA, demo_script_summary for presenter notes.

The sub-agent receives a fully hydrated task with actual content, not references:

```
[PRESENTATION TASK]
Customer: Acme Financial Services
POC: Oracle RAC → ADB-Dedicated Migration
Date: 2026-05-22

SLIDE 2 — Customer Challenge (use this exact content from POV):
"Acme Financial operates 3 Oracle RAC clusters totaling $2.1M/yr in
infrastructure and license costs. EOL support risk arrives in 18 months.
The target: 35% cost reduction while improving availability to 99.99%."

SLIDE 3 — Architecture (from diagram):
Single-region, us-chicago-1. ADB-Dedicated in a dedicated subnet. OCI
Compute (E5.Flex, 4 OCPU) for application tier. Load Balancer at ingress.
DRG for on-prem connectivity during migration window.

SLIDE 5 — Cost Estimate (from BOM v2):
Autonomous Database (2 ECPU):      $400/mo
OCI Compute E5.Flex (2 × 4 OCPU): $175/mo
Load Balancer (flexible):          $18/mo
Block Volume (1TB Balanced):       $51/mo
Monthly total:                     $644/mo
Vs. current: $2.1M/yr → $7,728/yr  (-96% — note: includes license savings)

SLIDE 6 — Why OCI (from research):
ADB-D runs on real Exadata infrastructure — not shared hardware emulation (vs. AWS RDS).
Data Safe provides autonomous security assessment, activity auditing, data masking
at no additional cost. OCI Security Zones enforce no-public-IP policy by compartment.

SLIDE 7 — Next Steps:
Wow moment: "Run Acme's own AR query — show query time on RAC vs. ADB-D, live."
Success criteria: "Migrate test DB in under 4 hours, show <$1.5M/yr equivalent cost."
[/PRESENTATION TASK]
```

This is a qualitatively different input than "here are some artifact keys."

---

## Task p55a — Background chat job support + Telegram notification

```
Context: The /api/chat/stream SSE connection holds open for the full turn.
SEs need to kick off POC generation during a meeting without holding a connection.
When done, they get a Telegram notification with the key finding, not just "complete."

Reuse without rebuilding:
  drawing_agent_server.py lines 334-360: _JOB_STORE, _new_job(), _complete_job(),
  _fail_job() — use exactly as-is
  drawing_agent_server.py ~line 2436: GET /api/job/{job_id} — already exists
  agent/notifications.py lines 62-75: TODO stub — implement

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p55a origin/main

---

CHANGE 1: skillforge/forge.py

Add one method to the Forge class (after run_turn, before any private methods):

```python
async def run_turn_background(
    self,
    message: str,
    history: list,
    context: dict,
    on_complete,
    on_error,
) -> None:
    """
    Run a full turn in the background.
    Calls on_complete(TurnResult) on success, on_error(Exception) on failure.
    No SSE — caller manages job lifecycle via callbacks.
    """
    try:
        result = await self.run_turn(message, history, context)
        await on_complete(result)
    except Exception as exc:
        await on_error(exc)
```

No Archie-specific logic here. Callbacks are injected by the caller.

---

CHANGE 2: drawing_agent_server.py

Add the background chat endpoint. Find where /api/chat/stream is defined and add
this endpoint immediately after:

```python
@app.post("/api/chat/background", status_code=202)
async def chat_background(request: ChatRequest):
    """
    Start a chat turn as a background job.
    Returns 202 with job_id + a pre-generated acknowledgment immediately.
    Poll GET /api/job/{job_id} for the result.
    """
    job_id = _new_job()
    customer_id = request.client_id or "default"

    # Pre-generate acknowledgment (template-based, no LLM call)
    customer_name = await _resolve_customer_name(customer_id)
    acknowledgment = (
        f"On it — starting analysis for **{customer_name}** in the background. "
        f"Usually takes 2–3 minutes. I'll send a Telegram notification when ready, "
        f"or ask me anything else in the meantime. Job: `{job_id}`"
    )

    async def on_complete(result) -> None:
        reply_preview = result.reply[:300] if result.reply else ""
        artifact_keys = list(result.artifacts.values()) if result.artifacts else []
        _complete_job(job_id, {
            "reply": result.reply,
            "artifacts": result.artifacts,
            "history_length": result.history_length,
        })
        await _notify_background_complete(customer_id, customer_name, reply_preview, artifact_keys)

    async def on_error(exc: Exception) -> None:
        _fail_job(job_id, str(exc))

    # Mirror how /api/chat/stream loads the session — adapt to match existing pattern
    store       = getattr(app.state, "object_store", None)
    text_runner = getattr(app.state, "text_runner",  None)
    session     = await _build_archie_session(customer_id, store, text_runner)

    asyncio.create_task(
        session.forge.run_turn_background(
            message=request.message,
            history=session.load_history(),
            context=session.context,
            on_complete=on_complete,
            on_error=on_error,
        )
    )

    return {"job_id": job_id, "status": "pending", "acknowledgment": acknowledgment}


async def _notify_background_complete(
    customer_id: str,
    customer_name: str,
    reply_preview: str,
    artifact_keys: list,
) -> None:
    """Build and fire a Telegram notification for a completed background job."""
    artifacts_note = ""
    if artifact_keys:
        artifacts_note = f"\nArtifacts: {', '.join(k.split('/')[-1] for k in artifact_keys[:3])}"
    message = (
        f"✅ *Archie: work complete* for {customer_name}\n"
        f"{reply_preview}{artifacts_note}"
    )
    # Fire-and-forget — import here to avoid circular import
    from agent.notifications import notify
    notify("background_complete", customer_id, detail=message)
```

Note: `_resolve_customer_name`, `_build_archie_session`, and `session.load_history()`
should mirror whatever the existing /api/chat/stream endpoint uses for session management.
Adapt names to match the actual codebase pattern.

---

CHANGE 3: agent/notifications.py

Replace the `_send` function body (lines ~62-75) with a working async-compatible
Telegram call:

```python
def _send(event: str, customer_id: str, detail: str) -> None:
    """
    Delivery backend. Logs and fires Telegram if configured.
    Synchronous wrapper — Telegram call is fire-and-forget via asyncio.
    """
    logger.info(
        "NOTIFY event=%s customer_id=%s detail=%r",
        event, customer_id, detail,
    )
    _fire_telegram(detail or f"[{event}] {customer_id}")


def _fire_telegram(text: str) -> None:
    """Schedule a Telegram message without blocking. Safe to call from sync context."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_telegram(text))
    except RuntimeError:
        pass  # no running loop — skip silently


async def _send_telegram(text: str) -> None:
    """Async Telegram send. Fire-and-forget — all failures are swallowed."""
    import os
    try:
        import httpx
        import yaml
        cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
        tg = cfg.get("telegram", {})
        if not tg.get("enabled", False):
            return
        token   = os.environ.get(tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN"), "")
        chat_id = os.environ.get(tg.get("chat_id_env",   "TELEGRAM_CHAT_ID"),   "")
        if not token or not chat_id:
            return
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            )
    except Exception:
        pass  # always fire-and-forget
```

Also add the `background_complete` event to the module docstring event list.

---

CHANGE 4: config.yaml

Add after the sub_agents section:

```yaml
telegram:
  enabled: false
  bot_token_env: "TELEGRAM_BOT_TOKEN"   # env var holding the bot token
  chat_id_env:   "TELEGRAM_CHAT_ID"     # env var holding the chat/group ID
```

---

CHANGE 5: ui/src/components/ChatInterface.tsx

Add background mode:
- "Background" toggle button in the chat input footer (icon: clock/moon)
- When active, POST to /api/chat/background instead of opening SSE stream
- On 202: append the `acknowledgment` text to the chat as an Archie message
  (same bubble style as a normal reply)
- Show a subtle "working in background" indicator in the chat thread (spinner + job_id)
- Poll GET /api/job/{job_id} every 5 seconds
- When status == "complete": append result.reply to chat, remove spinner
- When status == "error": show error in chat, remove spinner

---

Run ALL acceptance criteria:

  python3.11 -m py_compile skillforge/forge.py
  python3.11 -m py_compile drawing_agent_server.py
  python3.11 -m py_compile agent/notifications.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from skillforge.forge import Forge
  import inspect
  assert hasattr(Forge, 'run_turn_background'), 'FAIL: method missing'
  sig = inspect.signature(Forge.run_turn_background)
  for p in ['message', 'history', 'context', 'on_complete', 'on_error']:
      assert p in sig.parameters, f'FAIL: param {p!r} missing'
  print('PASS: run_turn_background present with correct signature')
  "

  python3.11 -c "
  import yaml
  cfg = yaml.safe_load(open('config.yaml'))
  tg = cfg.get('telegram', {})
  for k in ('enabled', 'bot_token_env', 'chat_id_env'):
      assert k in tg, f'FAIL: telegram.{k} missing from config.yaml'
  print('PASS: telegram config present')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.notifications import _send_telegram, _fire_telegram
  print('PASS: Telegram functions importable')
  import inspect
  src = inspect.getsource(_send_telegram)
  assert 'api.telegram.org' in src, 'FAIL: Telegram API URL missing'
  assert 'enabled' in src, 'FAIL: enabled check missing'
  print('PASS: Telegram implementation looks correct')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55b — POC Strategist hat (expert thinking lens)

```
Context: This task is ONLY the hat file. No Python code. The hat is the most
important artifact in p55 — it determines the quality of POC recommendations.
Read the POC Strategist Hat Specification section of this plan carefully before
writing. Read agent/hats/infra_tech_research.md and agent/hats/oci_bom_expert.md
as format references. Match the format exactly.

IMPORTANT: Branch from origin/main. Independent of all other p55 tasks.

  git fetch origin
  git checkout -b claude/p55b origin/main

---

FILE: agent/hats/oci_poc_strategist.md

This is a NEW file. It must follow the exact format of oci_bom_expert.md:
YAML frontmatter + 10 named Markdown sections.

### YAML Frontmatter requirements:

```yaml
---
version: "1.0"
display_name: "OCI POC Strategist"
hat_rules:
  when_to_activate:
    - "user asks what POC to build for a customer"
    - "user says 'what should we demo', 'what POC would close this deal', 'help me plan a POC'"
    - "SE has rough customer requirements and no POC direction yet"
    - "user asks 'what are our options', 'what would resonate with this customer'"
    - "generate_poc_plan is about to be called"
  can_hand_off_to:
    - "diagram_for_oci"
    - "oci_bom_expert"
    - "oci_customer_pov_writer"
  suggested_next_hat: "diagram_for_oci"
  resume_condition: "POC option revision or alternative angle requested"
memory_focus:
  priority_fields:
    - pain_statement
    - current_platform
    - customer_industry
    - deal_stage
    - timeline
    - budget_signal
    - competitive_context
    - decision_makers
    - poc_options
    - poc_recommendation
  summary_style: "poc_strategy_oriented"
  include_full_memory: false
  emphasis: >
    Focus on customer pain, current platform, deal stage, timeline pressure,
    budget signals, competitive context, and any existing POC history.
    The goal is to recommend the POC most likely to close this specific deal.
coordination:
  triggers:
    - "POC plan generated"
    - "user confirms a POC option"
  recommended_hats:
    - "diagram_for_oci"
    - "oci_bom_expert"
  parallel_with:
    - "infra_tech_research"
  suggested_next_hat: "diagram_for_oci"
  handoff_message: >
    POC plan delivered. When the SE confirms an option, fan out to
    diagram + BOM + JEP + Terraform + presentation simultaneously.
---
```

### Section: Core Principles

Must encode (verbatim guidance for the hat wearer):

1. **Lead with the deal, not the demo.** The POC that closes the deal is not always
   the most technically impressive — it's the one that directly proves the customer's
   stated pain. If the CFO mentioned cost three times, the wow moment must show
   a cost number, not a performance graph.

2. **Name the archetype before proposing options.** Match the customer to a known
   POC pattern: Oracle DB Migration / OKE Modernization / HeatWave Acceleration /
   AI/ML on OCI / Cost Optimization / Disaster Recovery. The archetype determines
   the success pattern.

3. **Deal stage changes everything.** Discovery → offer 3 options. Evaluation → one
   focused recommendation. Decision → risk-reduction POC, show the exit ramp.
   If deal stage is unknown, default to Evaluation and surface the assumption.

4. **Industry-specific compliance wins.** Financial Services: lead with Security Zones
   and Data Safe before cost. Healthcare: BAA + encryption + data residency. These are
   not features — they are deal requirements in disguise.

5. **Anticipate what kills POCs.** No agreed success criteria before the demo.
   Wrong audience. Wow moment buried at the end. Build time underestimated.
   Competitor FUD not pre-addressed. Pre-provisioning skipped.
   Name the top 2 risks for the recommended POC and give concrete mitigations.

6. **Specificity about the wow moment.** "Show performance" is not a wow moment.
   "Run Acme's own AR reconciliation query against ADB-D and show the time delta
   plus cost equivalence live" is a wow moment. One sentence. 30 seconds of the demo.

7. **The sub-agent brief is complete.** The poc_strategist sub-agent has no other
   context. The brief must include: customer archetype, industry, deal stage, the
   specific angle being explored, success pattern steps, wow moment, top risks,
   and any pre-demo preparation required.

### Section: POC Archetypes

Include the full archetype table from this spec (see "POC archetypes with success
patterns" above) as a reference table in this section. The hat wearer should be
able to look up the archetype by customer profile.

### Section: Quality Bar

10 checklist items — see "Hat Quality Bar" section in this spec.

### Section: Output Contract

The poc_strategist sub-agent returns one option as JSON (per angle). The handler
synthesizes 3 options into a combined response. The combined response saved to
document_store must have this structure:

```json
{
  "poc_options": [
    {
      "option_name": "Oracle RAC → ADB-Dedicated migration for Acme Financial",
      "angle": "migration_modernization",
      "relevance_score": 9,
      "executability_hours": 4,
      "cost_effectiveness": "ADB-D ~$640/mo vs. ~$175K/yr on-prem license+hw → 96% reduction",
      "security_highlights": ["OCI Security Zones", "Data Safe", "ADB-D dedicated VCN"],
      "oci_services": ["Autonomous Database Dedicated", "OCI Compute E5.Flex", "VCN"],
      "wow_moment": "Run Acme's own AR query on ADB-D — show time delta + cost calculator live",
      "demo_script_summary": "Provision ADB-D in 20 min → Data Pump import of test schema → run AR query side-by-side → show OCI cost vs. current spend"
    }
  ],
  "recommendation": {
    "poc_name": "Oracle RAC → ADB-Dedicated migration for Acme Financial",
    "rationale": "CFO mentioned cost 3× and timeline is 3 weeks — migration directly proves the $2M pain in 4h build time. Financial services: Security Zones + Data Safe address compliance before the pricing conversation starts.",
    "success_criteria": "Migrate test DB in under 4 hours, show query performance equivalent or better, cost estimate < $1.5M/yr",
    "pre_demo_checklist": ["Run Oracle DB Compatibility Checker 48h before", "Pre-provision ADB-D instance day before demo", "Confirm region availability for ADB-D shape"],
    "wow_moment": "Run Acme's own AR query on ADB-D — show time delta + cost calculator live"
  }
}
```

### Section: Pre-Action Checklist

This is the expert reasoning gate. Must confirm before calling generate_poc_plan:

Required inputs — default or clarify:
- pain_statement: most important input. If absent → NEEDS_CLARIFICATION: "What is the
  customer's primary pain (cost, performance, risk, compliance, time-to-market)?"
- current_platform: second most important. If absent → NEEDS_CLARIFICATION: "What is
  the customer running today (on-prem Oracle, AWS, Azure, bare metal)?"
- customer_industry: default "enterprise technology" if absent — document assumption
- deal_stage: default "evaluation" if absent — document assumption
- timeline: default "flexible" if absent — document assumption
- competitive_context: use "none stated" if absent — flag if remembered later

Then perform the EXPERT ASSESSMENT following the structure in this spec:
CUSTOMER ARCHETYPE → DEAL STAGE READING → POC SELECTION REASONING (per angle) →
RECOMMENDED POC → SUCCESS PATTERN → WOW MOMENT → TOP POC RISKS → COMPETITIVE CONTEXT →
PROACTIVE FLAG → SUB-AGENT TASK (one per angle).

End pre-action with exactly 3 SUB-AGENT TASK blocks (one per angle), each labeled:
  [SUB-AGENT TASK — migration_modernization]
  ...
  [/SUB-AGENT TASK]

  [SUB-AGENT TASK — performance_scale_ai]
  ...
  [/SUB-AGENT TASK]

  [SUB-AGENT TASK — cost_optimization_tco]
  ...
  [/SUB-AGENT TASK]

### Section: Post-Action Review

After generate_poc_plan returns the combined 3-option response:

Mandatory checks:
- 3 options present (one per angle)
- recommended option's rationale cites ≥2 specific facts from KNOWN FACTS
  (not generic statements like "this is a good fit")
- WOW MOMENT is a concrete ≤2 sentence action (not "show performance improvement")
- pre_demo_checklist has ≥2 items
- option names are customer-specific (contain customer name or workload description)
- All executability_hours ≤ 8
- artifact_key present

Decision:
- All checks pass → approve for critic
- Generic rationale → iterate with correction: "Rationale must cite specific facts
  from KNOWN FACTS. Rewrite with: [list the available facts]"
- Missing wow moment specificity → iterate: "Wow moment must be a 30-second demo
  action. Specify: who does what, what the customer sees, what the number is."

---

Run ALL acceptance criteria:

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  import agent.hat_engine as he
  hats = he.load_hats()
  assert 'oci_poc_strategist' in hats, f'FAIL: hat not discovered. Found: {list(hats.keys())}'
  print('PASS: oci_poc_strategist hat discovered')
  "

  python3.11 -c "
  from pathlib import Path
  hat = Path('agent/hats/oci_poc_strategist.md').read_text()
  # Check critical content
  checks = [
      ('Lead with the deal', 'Core Principles: deal-first'),
      ('archetype', 'archetype pattern matching'),
      ('NEEDS_CLARIFICATION', 'clarification gate'),
      ('SUB-AGENT TASK', 'sub-agent task blocks'),
      ('migration_modernization', 'migration angle'),
      ('performance_scale_ai', 'AI angle'),
      ('cost_optimization_tco', 'cost angle'),
      ('wow_moment', 'wow moment field'),
      ('pre_demo_checklist', 'pre-demo checklist'),
      ('deal_stage', 'deal stage logic'),
  ]
  for content, label in checks:
      assert content in hat, f'FAIL: {label!r} not found — missing {content!r}'
  print('PASS: all critical hat sections present')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Only create/modify agent/hats/oci_poc_strategist.md. No other files.
```

---

## Task p55c — POC Strategist tool (sub-agent + handler + registration)

```
Context: Task p55b created the hat (the thinking lens). This task creates the
execution layer: sub-agent, handler, and registration. The handler makes 3
parallel sub-agent calls (not 1) and supports two modes: explore and confirm.

Confirm mode is how the fan-out works — no regex, no Python detection logic.
The LLM (via system prompt instructions in p55d) decides when to call confirm.

Depends on: p55b (hat must exist before registering the tool with requires_hat).

IMPORTANT: Branch from origin/main (or cherry-pick p55b if not yet merged).

  git fetch origin
  git checkout -b claude/p55c origin/main

---

FILE 1: sub_agents/poc_strategist/__init__.py  (empty)

---

FILE 2: sub_agents/poc_strategist/config.yaml

```yaml
name: poc_strategist
port: 8089
llm:
  model_id: ""       # inherits root config.yaml inference.model_id
  max_tokens: 2048
  temperature: 0.6
```

---

FILE 3: sub_agents/poc_strategist/system_prompt.md

```markdown
# POC Strategist Sub-Agent

You are the OCI POC strategy analyst for Archie. Given a customer context and a
specific exploration angle, evaluate ONE POC option and return it as structured JSON.

You receive a complete brief from Archie (the manager). The brief includes:
customer archetype, industry, deal stage, the angle you are evaluating, the
success pattern, and any pre-demo preparation requirements. Use this brief.
Do not invent context not in the brief.

## Your Job

Evaluate the single POC angle specified in your brief. Produce one option with:

- **option_name**: Customer-specific. Include the customer name and workload.
  "Oracle RAC → ADB-Dedicated migration for Acme Financial" not "Database POC."
- **angle**: Exactly one of: migration_modernization / performance_scale_ai / cost_optimization_tco
- **relevance_score**: 1–10. Does this POC directly prove the customer's stated pain?
  A 10 means: if this demo succeeds, there is no reasonable reason to say no.
  A 5 means: it demonstrates OCI capability but doesn't directly address the pain.
- **executability_hours**: Integer. How many hours to build + demo-ready?
  Include: environment provisioning, data loading, test validation. Do not underestimate.
  Maximum 8 hours for a viable POC.
- **cost_effectiveness**: String. Defensible OCI cost range and what it compares to.
  Reference customer's current spend if stated in the brief. Be specific: "$640/mo vs. ~$175K/yr."
- **security_highlights**: List of 2–4 OCI security controls relevant to this customer.
  Use exact OCI service/feature names: "OCI Security Zones", "Data Safe", "OCI Vault KMS."
- **oci_services**: List of specific OCI service names. Minimum 3.
  "Autonomous Database Dedicated" not "managed database."
- **wow_moment**: One sentence. A 30-second demo action the customer will remember.
  Must reference the customer's actual pain: "Run [customer]'s [specific query/workload]..."
  Not "show performance." Not "demonstrate capabilities."
- **demo_script_summary**: 2–3 sentences. What the SE does, step by step, to reach the wow moment.
- **pre_demo_checklist**: 2–4 concrete preparation steps. What the SE must do BEFORE the demo.
  Example: "Pre-provision ADB-D instance day before demo — provisioning takes 45 minutes."

## Output Format

Return exactly this JSON (no markdown, no prose):

{
  "option_name": "string",
  "angle": "migration_modernization",
  "relevance_score": 9,
  "executability_hours": 4,
  "cost_effectiveness": "string",
  "security_highlights": ["string"],
  "oci_services": ["string"],
  "wow_moment": "string",
  "demo_script_summary": "string",
  "pre_demo_checklist": ["string"]
}

If the brief is insufficient to produce a scored option, return:
{"type": "needs_input", "reply": "One sentence: what is missing from the brief."}
```

---

FILE 4: sub_agents/poc_strategist/server.py

Copy sub_agents/pov/server.py exactly. Adapt:
- agent_name = "poc_strategist"
- AgentCard: name, description="OCI POC strategy analyst — evaluates one POC angle per call",
  inputs=["task", "angle", "customer_context"], required=["task"]
- Port from config.yaml (8089)
- Remove revision/prior_version handling (not applicable)

---

CHANGE 5: agent/tools/specialists.py

Add at the bottom of the file (after TechResearchHandler). This handler supports
TWO modes controlled by the `action` argument:

```python
class PocStrategistHandler:
    """
    POC Strategist tool handler. Two modes:

    action="explore" (default):
        Makes 3 parallel sub-agent calls (migration, AI/ML, cost angles).
        Returns ToolResult(status="ok") with the 3 options and recommendation.

    action="confirm" + confirmed_option_name:
        Looks up the confirmed option from memory.
        Returns ToolResult(status="parallel", parallel_tools=[5 artifact tools])
        triggering the fan-out via Forge's existing asyncio.gather() path.
    """
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store         = store
        self._customer_id   = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        import asyncio
        import json as _json
        from agent.sub_agent_client import call_sub_agent
        from skillforge.types import ParallelToolCall

        action = (args.get("action") or "explore").lower().strip()
        dc     = getattr(memory, "decision_context", {}) or {}

        # ── Confirm mode: fan out all 5 artifacts ─────────────────────────
        if action == "confirm":
            confirmed_name = (args.get("confirmed_option_name") or "").strip()
            poc_options    = dc.get("poc_options", [])

            if not poc_options:
                return ToolResult(
                    status="needs_input",
                    summary="No POC options in memory. Run generate_poc_plan first.",
                    clarification="Please generate a POC plan first — I don't have any options to confirm yet.",
                )

            # Find the confirmed option by name (case-insensitive, partial match)
            option = next(
                (o for o in poc_options if confirmed_name.lower() in o.get("option_name", "").lower()),
                poc_options[0],  # default to recommendation if name not matched
            )

            poc_name = option.get("option_name", "OCI POC")
            services = option.get("oci_services", [])
            region   = dc.get("region", "us-chicago-1")

            return ToolResult(
                status="parallel",
                summary=f"POC confirmed: {poc_name}. Generating all 5 artifacts in parallel...",
                parallel_tools=[
                    ParallelToolCall(
                        tool="generate_diagram",
                        args={"_user_message": (
                            f"Create OCI architecture diagram for POC: {poc_name}. "
                            f"Services: {', '.join(services)}. Region: {region}."
                        )},
                    ),
                    ParallelToolCall(
                        tool="generate_bom",
                        args={"prompt": (
                            f"Generate BOM for POC: {poc_name}. "
                            f"Services: {', '.join(services)}. Region: {region}."
                        )},
                    ),
                    ParallelToolCall(
                        tool="generate_jep",
                        args={"_user_message": (
                            f"Create JEP execution plan for POC: {poc_name}. "
                            f"Build sequence: {option.get('pre_demo_checklist', [])}. "
                            f"Demo script: {option.get('demo_script_summary', '')}."
                        )},
                    ),
                    ParallelToolCall(
                        tool="generate_terraform",
                        args={"_user_message": (
                            f"Generate Terraform for POC: {poc_name}. "
                            f"Services: {', '.join(services)}. Region: {region}."
                        )},
                    ),
                    ParallelToolCall(
                        tool="generate_presentation",
                        args={
                            "_user_message": f"Create client PowerPoint deck for POC: {poc_name}.",
                            "poc_option": option,
                        },
                    ),
                ],
            )

        # ── Explore mode: 3 parallel sub-agent calls ──────────────────────
        pain     = dc.get("pain_statement", "")
        platform = dc.get("current_platform", "")

        if not pain or not platform:
            return ToolResult(
                status="needs_input",
                summary="Need customer pain statement and platform before planning POC options.",
                clarification=(
                    "What is the customer's primary pain (cost, performance, risk, compliance) "
                    "and what platform are they currently running on?"
                ),
            )

        # The hat's pre-action generates 3 SUB-AGENT TASK blocks — one per angle.
        # Extract them from args if present (injected by the expert pre-action step),
        # or fall back to building generic briefs from memory.
        task_blocks = args.get("_sub_agent_tasks") or {}
        base_context = (
            f"Customer: {self._customer_name}\n"
            f"Industry: {dc.get('customer_industry', 'enterprise')}\n"
            f"Pain: {pain}\n"
            f"Platform: {platform}\n"
            f"Deal stage: {dc.get('deal_stage', 'evaluation')}\n"
            f"Timeline: {dc.get('timeline', 'flexible')}\n"
            f"Competitive context: {dc.get('competitive_context', 'none stated')}\n"
            f"Additional context: {args.get('_user_message', '')}"
        )

        results = await asyncio.gather(
            call_sub_agent(
                "poc_strategist",
                task=task_blocks.get("migration_modernization") or base_context,
                engagement_context={"angle": "migration_modernization", "customer_id": self._customer_id},
                trace_id=trace_id,
            ),
            call_sub_agent(
                "poc_strategist",
                task=task_blocks.get("performance_scale_ai") or base_context,
                engagement_context={"angle": "performance_scale_ai", "customer_id": self._customer_id},
                trace_id=trace_id,
            ),
            call_sub_agent(
                "poc_strategist",
                task=task_blocks.get("cost_optimization_tco") or base_context,
                engagement_context={"angle": "cost_optimization_tco", "customer_id": self._customer_id},
                trace_id=trace_id,
            ),
            return_exceptions=True,
        )

        options = []
        for r in results:
            if isinstance(r, Exception):
                continue
            try:
                raw    = getattr(r, "result", "") or ""
                parsed = _json.loads(raw)
                if parsed.get("option_name") and parsed.get("relevance_score"):
                    options.append(parsed)
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
                "poc_name":          rec.get("option_name"),
                "rationale":         (
                    f"Highest composite score: relevance {rec.get('relevance_score')}/10, "
                    f"{rec.get('executability_hours')}h build. "
                    f"{rec.get('wow_moment', '')}"
                ),
                "success_criteria":  rec.get("wow_moment", ""),
                "pre_demo_checklist": rec.get("pre_demo_checklist", []),
                "demo_script":       rec.get("demo_script_summary", ""),
            },
        }

        key = f"poc_plan/{self._customer_id}/v1.json"
        await self._store.save_doc(key, _json.dumps(payload, indent=2))

        return ToolResult(
            status="ok",
            summary=f"Explored 3 POC options. Recommended: {rec.get('option_name')} ({rec.get('relevance_score')}/10 relevance, {rec.get('executability_hours')}h build)",
            artifact_key=key,
            data=payload,
        )
```

---

CHANGE 6: agent/archie_wiring.py

Add import: from agent.tools.specialists import ..., PocStrategistHandler

After the generate_waf registration, add:

```python
forge.register_tool(
    "generate_poc_plan",
    PocStrategistHandler(
        store=store,
        customer_id=customer_id,
        customer_name=customer_name,
    ),
    description=(
        "Plan a technical POC. "
        "action='explore' (default): explores 3 parallel POC angles (migration, AI/ML, cost) "
        "and returns ranked options with wow moments, build times, and risk assessments. "
        "action='confirm' + confirmed_option_name: fans out all 5 artifacts in parallel "
        "(diagram, BOM, JEP, Terraform, presentation). "
        "Call with action='explore' when the SE needs POC direction. "
        "Call with action='confirm' after the user selects an option."
    ),
    args={
        "action": ArgSchema(
            description="'explore' to generate options, 'confirm' to start artifact generation.",
            type="string",
            required=False,
        ),
        "confirmed_option_name": ArgSchema(
            description="The option_name the user confirmed. Required when action='confirm'.",
            type="string",
            required=False,
        ),
    },
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_poc_strategist",
)
```

CHANGE 7: config.yaml — add to sub_agents section:
  poc_strategist: "http://localhost:8089"

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/tools/specialists.py
  python3.11 -m py_compile agent/archie_wiring.py
  python3.11 -m py_compile sub_agents/poc_strategist/server.py

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from agent.tools.specialists import PocStrategistHandler
  import inspect
  src = inspect.getsource(PocStrategistHandler.__call__)
  assert 'asyncio.gather' in src, 'FAIL: 3 parallel calls missing'
  assert 'action == \"confirm\"' in src or \"action == 'confirm'\" in src, 'FAIL: confirm mode missing'
  assert 'ParallelToolCall' in src, 'FAIL: ParallelToolCall not used'
  assert 'generate_presentation' in src, 'FAIL: presentation not in fan-out'
  print('PASS: handler has explore + confirm modes and parallel fan-out')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  from agent.archie_wiring import build_forge
  forge = build_forge(store=MagicMock(), customer_id='test', customer_name='Test',
                      text_runner=MagicMock(), step3_planning=False)
  spec = forge._registry.get('generate_poc_plan')
  assert spec is not None, 'FAIL: generate_poc_plan not registered'
  assert spec.requires_hat == 'oci_poc_strategist', f'FAIL: wrong hat: {spec.requires_hat}'
  assert spec.memory_contract, 'FAIL: memory_contract not set'
  assert 'action' in (spec.args or {}), 'FAIL: action arg missing'
  print('PASS: generate_poc_plan registered correctly')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55d — Archie system prompt: POC workflow sequencing

```
Context: With the POC Strategist hat (p55b) and tool (p55c) in place, Archie
needs explicit sequencing rules for the POC workflow. This task adds to
_TOOL_SEQUENCING_RULES in archie_wiring.py. Prompt-first: the LLM decides
when to call action='confirm', not Python.

Depends on: p55b and p55c merged.

IMPORTANT: Branch from origin/main (or from p55c if not yet merged).

  git fetch origin
  git checkout -b claude/p55d origin/main

---

CHANGE 1: agent/archie_wiring.py — update _TOOL_SEQUENCING_RULES

Find _TOOL_SEQUENCING_RULES and add this section at the end, before the closing quotes:

```
### POC Planning Workflow

When the SE needs to know what to build for a customer:

1. Call generate_poc_plan (default: action="explore"). This runs 3 parallel
   evaluations and returns ranked options. Each option includes a relevance score,
   build time, wow moment, and pre-demo checklist.

2. Present the options clearly. For each option: name, relevance score (X/10),
   build time (Xh), wow moment, and key risks. Give your recommendation with
   rationale citing ≥2 specific facts from the customer context.
   End with a clear invitation: "Which option would you like to proceed with?"

3. Wait for the user to confirm a specific option before generating any artifacts.
   When the user selects — by number ("option 1"), by name, by description
   ("the migration one"), or by affirmation ("that one", "let's do it", "go") —
   extract the confirmed_option_name from the options list and call:
     generate_poc_plan(action="confirm", confirmed_option_name="[exact option name]")

4. The confirm call fans out all 5 artifacts simultaneously. When all complete,
   present them as a coherent package:
   "Your POC kit for [option_name] is ready: architecture diagram, BOM (~$X/mo),
   JEP execution plan, Terraform scripts, and client deck. [Download links.]"

5. Do NOT generate artifacts before the user confirms an option.
6. Do NOT call generate_poc_plan with action="explore" again after confirmation.
7. If the user says "try option 2 instead" or "actually use the AI angle", call
   generate_poc_plan(action="confirm", confirmed_option_name="[option 2 name]").
8. If you cannot determine which option the user means, ask once:
   "Which option — the [name1] (Xh, Y/10) or the [name2] (Xh, Y/10)?"
```

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/archie_wiring.py

  python3.11 -c "
  from pathlib import Path
  src = Path('agent/archie_wiring.py').read_text()
  checks = [
      ('POC Planning Workflow', 'POC workflow section header'),
      (\"action=\\\"confirm\\\"\", 'confirm mode reference'),
      ('confirmed_option_name', 'confirmed_option_name arg reference'),
      ('Do NOT generate artifacts before', 'no-artifacts-before-confirm rule'),
  ]
  for content, label in checks:
      assert content in src, f'FAIL: {label!r} missing — looking for {content!r}'
  print('PASS: POC workflow sequencing rules present in archie_wiring.py')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Only modify agent/archie_wiring.py (_TOOL_SEQUENCING_RULES section). No other files.
```

---

## Task p55e — Presentation: hat + synthesis handler + sub-agent + renderer

```
Context: No PPTX capability exists today. The presentation must synthesize
actual artifact content — not just reference keys. The hat loads BOM content,
research findings, and POV narrative BEFORE calling the sub-agent, so the deck
tells the customer's specific story.

Reference: https://github.com/aruanurag/oci-architecture-codex-skill
Uses oracle-oci-architecture-toolkit-v24.1.pptx as master stencil.
Same pattern as OCI_Library.xml for draw.io icons.

IMPORTANT: Branch from origin/main. Independent of p55a/b/c/d.

  git fetch origin
  git checkout -b claude/p55e origin/main

---

CHANGE 1: requirements.txt — add:
  python-pptx>=1.0.2

---

FILE 2: agent/hats/oci_presentation_writer.md

New file. Format follows oci_bom_expert.md.

Key YAML frontmatter:
```yaml
version: "1.0"
display_name: "OCI Presentation Writer"
hat_rules:
  when_to_activate:
    - "generate_presentation is about to be called"
    - "user asks for a PowerPoint, deck, slides, or client presentation"
    - "POC artifacts are complete and a client deck is the next step"
memory_focus:
  priority_fields:
    - poc_recommendation
    - customer_name
    - pain_statement
    - bom_summary
    - jep_phases
    - diagram_artifact_key
    - pov_artifact_key
    - research_artifact_key
  summary_style: "synthesis_oriented"
  include_full_memory: false
coordination:
  parallel_with: ["generate_diagram", "generate_bom", "generate_jep", "generate_terraform"]
  suggested_next_hat: null
```

Pre-Action Checklist:

The hat MUST perform content synthesis before the sub-agent call:

1. POC recommendation: if absent → NEEDS_CLARIFICATION: "No POC has been confirmed yet.
   Use generate_poc_plan first, then confirm an option."
2. Customer name: if absent → NEEDS_CLARIFICATION: "What is the customer's name?"
3. Load BOM artifact content if artifact key exists in memory:
   - Extract: monthly_total, top 5 line items (service, qty, cost)
   - If no BOM: note "BOM not yet generated — use TBD for cost figures"
4. Load research artifact content if exists:
   - Extract: recommendation rationale, top 2 risks, competitive differentiators
   - If no research: use POC recommendation's oci_services and security_highlights
5. Load POV artifact content if exists:
   - Extract: executive_summary (first 2 paragraphs)
   - If no POV: use pain_statement from memory

End pre-action with a [PRESENTATION BRIEF] block:

```
[PRESENTATION BRIEF]
Customer: Acme Financial Services
POC: Oracle RAC → ADB-Dedicated Migration
Date: 2026-05-22

SLIDE 2 — Customer Challenge:
[Verbatim from POV executive_summary, or from pain_statement if no POV]

SLIDE 3 — Architecture:
[Description from diagram topology, or from poc_recommendation.oci_services if no diagram]
Region: [region from memory]
Key components: [list oci_services]

SLIDE 5 — Cost Estimate:
[Verbatim from BOM artifact: line items and monthly_total]
[Or TBD if no BOM available]

SLIDE 6 — Why OCI:
[From research recommendation_rationale and competitive differentiators]
[Or from poc_recommendation.security_highlights if no research]

SLIDE 7 — Next Steps:
Wow moment: [from poc_recommendation.wow_moment]
Success criteria: [from poc_recommendation.success_criteria]
[/PRESENTATION BRIEF]
```

Post-Action Review: 7 slides, no placeholder text, customer name on title,
artifact_key ends in .pptx, BOM numbers match if BOM was loaded.

---

FILE 3: sub_agents/presentation/__init__.py  (empty)

---

FILE 4: sub_agents/presentation/config.yaml

```yaml
name: presentation
port: 8090
llm:
  model_id: ""
  max_tokens: 2048
  temperature: 0.2
```

---

FILE 5: sub_agents/presentation/system_prompt.md

```markdown
# Presentation Sub-Agent

You produce a 7-slide OCI POC client deck as a JSON slide specification.
You receive a complete [PRESENTATION BRIEF] with actual customer content —
use it verbatim. Do not substitute placeholders for provided content.

## Slide Structure (always exactly 7)

1. title       — POC name, customer name, date
2. challenge   — from SLIDE 2 content in brief (customer's pain, current state)
3. architecture — from SLIDE 3 content in brief (services, topology description)
4. services    — key OCI services with one-liner each (from oci_services in brief)
5. cost        — from SLIDE 5 content in brief (BOM line items, monthly total)
6. why_oci     — from SLIDE 6 content in brief (differentiators, risk mitigations)
7. next_steps  — from SLIDE 7 content in brief (wow moment, success criteria, CTA)

## Presenter Notes

Every slide must have presenter_notes. Include:
- Slide 1: "Open by confirming agenda and time. Introduce Oracle team."
- Slide 2: "This is THEIR situation — confirm it resonates before proceeding."
- Slide 5: "Use actual BOM numbers from slide — do not estimate during the presentation."
- Other slides: talking points and expected objections based on the brief content.

## Output Format

Return exactly this JSON (no markdown wrapper, no prose):

{
  "slides": [
    {"slide_number": 1, "layout": "title", "title": "string", "subtitle": "string", "presenter_notes": "string"},
    {"slide_number": 2, "layout": "bullets", "title": "string", "bullets": ["string"], "presenter_notes": "string"},
    {"slide_number": 3, "layout": "architecture", "title": "string", "oci_services": [{"name": "string", "icon": "OCI_icon_name"}], "topology_description": "string", "presenter_notes": "string"},
    {"slide_number": 4, "layout": "services", "title": "string", "services": [{"name": "string", "description": "string"}], "presenter_notes": "string"},
    {"slide_number": 5, "layout": "table", "title": "string", "rows": [{"service": "string", "qty": "string", "monthly_cost": "string"}], "total": "string", "presenter_notes": "string"},
    {"slide_number": 6, "layout": "bullets", "title": "string", "bullets": ["string"], "presenter_notes": "string"},
    {"slide_number": 7, "layout": "next_steps", "title": "string", "bullets": ["string"], "cta": "string", "presenter_notes": "string"}
  ]
}
```

---

FILE 6: sub_agents/presentation/scripts/resolve_oci_powerpoint_icon.py

```python
OCI_ICON_MAP = {
    "Autonomous Database Serverless":  "OCI_Autonomous_Database",
    "Autonomous Database Dedicated":   "OCI_Autonomous_Database",
    "Autonomous Database":             "OCI_Autonomous_Database",
    "ADB":                             "OCI_Autonomous_Database",
    "OCI Compute":                     "OCI_Compute_Instance",
    "Compute":                         "OCI_Compute_Instance",
    "Virtual Cloud Network":           "OCI_VCN",
    "VCN":                             "OCI_VCN",
    "OKE":                             "OCI_Container_Engine_for_Kubernetes",
    "Container Engine for Kubernetes": "OCI_Container_Engine_for_Kubernetes",
    "Load Balancer":                   "OCI_Load_Balancer",
    "Object Storage":                  "OCI_Object_Storage_Bucket",
    "OCI Vault":                       "OCI_Key_Management",
    "Vault":                           "OCI_Key_Management",
    "MySQL HeatWave":                  "OCI_MySQL_HeatWave",
    "HeatWave":                        "OCI_MySQL_HeatWave",
    "Data Science":                    "OCI_Data_Science",
    "Generative AI":                   "OCI_Generative_AI",
    "FastConnect":                     "OCI_FastConnect",
    "DRG":                             "OCI_Dynamic_Routing_Gateway",
    "WAF":                             "OCI_Web_Application_Firewall",
    "Data Safe":                       "OCI_Data_Safe",
}

def resolve_icon(service_name: str) -> str | None:
    name = (service_name or "").strip()
    return OCI_ICON_MAP.get(name) or OCI_ICON_MAP.get(name.split(" ")[0])
```

---

FILE 7: sub_agents/presentation/scripts/render_oci_powerpoint.py

```python
"""
Renders a 7-slide OCI POC deck from a JSON spec using python-pptx.
Uses oracle-oci-architecture-toolkit-v24.1.pptx for OCI icon stencils.
Falls back to text labels gracefully if toolkit is not present.
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
ORACLE_LIGHT = RGBColor(0xF5, 0xF5, 0xF5)


def render(spec: dict[str, Any], output_path: str) -> None:
    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    for slide_spec in sorted(spec.get("slides", []), key=lambda s: s.get("slide_number", 0)):
        slide  = prs.slides.add_slide(blank)
        layout = slide_spec.get("layout", "bullets")
        title  = slide_spec.get("title", "")
        notes  = slide_spec.get("presenter_notes", "")

        _set_background(slide, layout)
        _add_title(slide, title, layout)
        _render_body(slide, slide_spec, layout)

        if notes:
            slide.notes_slide.notes_text_frame.text = notes

    prs.save(output_path)


def _set_background(slide, layout: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = ORACLE_DARK if layout == "title" else ORACLE_WHITE


def _add_title(slide, text: str, layout: str) -> None:
    color = ORACLE_WHITE if layout == "title" else ORACLE_DARK
    _textbox(slide, text, Inches(0.5), Inches(0.25), Inches(12.3), Inches(1.2),
             size=32 if layout == "title" else 26, color=color, bold=True)


def _render_body(slide, spec: dict, layout: str) -> None:
    if layout == "title":
        subtitle = spec.get("subtitle", "")
        if subtitle:
            _textbox(slide, subtitle, Inches(1.5), Inches(4.0), Inches(10), Inches(1.2),
                     size=22, color=ORACLE_WHITE)
    elif layout == "architecture":
        services = spec.get("oci_services", [])
        desc     = spec.get("topology_description", "")
        _render_architecture(slide, services, desc)
    elif layout == "table":
        _render_table(slide, spec.get("rows", []), spec.get("total", ""))
    elif layout == "next_steps":
        items = spec.get("bullets", [])
        _render_bullets(slide, items)
        cta = spec.get("cta", "")
        if cta:
            _textbox(slide, cta, Inches(0.5), Inches(6.4), Inches(12.3), Inches(0.7),
                     size=16, color=ORACLE_RED, bold=True)
    elif layout in ("bullets", "services"):
        items = spec.get("bullets") or [
            f"{s['name']}: {s['description']}" for s in spec.get("services", [])
        ]
        _render_bullets(slide, items)


def _render_bullets(slide, items: list[str]) -> None:
    if not items:
        return
    txBox = slide.shapes.add_textbox(Inches(0.5), Inches(1.8), Inches(12.3), Inches(5.2))
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = f"• {item}"
        p.font.size  = Pt(18)
        p.font.color.rgb = ORACLE_DARK
        p.space_after = Pt(4)


def _render_architecture(slide, services: list[dict], description: str) -> None:
    from sub_agents.presentation.scripts.resolve_oci_powerpoint_icon import resolve_icon
    y = Inches(1.8)
    for svc in services[:6]:
        icon_name = resolve_icon(svc.get("name", ""))
        if icon_name and TOOLKIT_PATH.exists():
            _copy_icon(icon_name, slide)
        _textbox(slide, svc.get("name", ""), Inches(0.5), y, Inches(3.5), Inches(0.4), size=14)
        y += Inches(0.7)
    if description:
        _textbox(slide, description, Inches(5.0), Inches(1.8), Inches(7.8), Inches(4.5), size=14)


def _render_table(slide, rows: list[dict], total: str) -> None:
    y = Inches(1.8)
    header = "Service                              Qty           Monthly Cost"
    _textbox(slide, header, Inches(0.5), y, Inches(12.3), Inches(0.4),
             size=13, color=ORACLE_DARK, bold=True)
    y += Inches(0.45)
    for row in rows:
        line = f"{row.get('service',''):<36} {row.get('qty',''):<14} {row.get('monthly_cost','')}"
        _textbox(slide, line, Inches(0.5), y, Inches(12.3), Inches(0.4), size=13)
        y += Inches(0.38)
    if total:
        _textbox(slide, f"Monthly Total: {total}", Inches(0.5), y + Inches(0.2),
                 Inches(6), Inches(0.5), size=18, bold=True, color=ORACLE_RED)


def _copy_icon(shape_name: str, target_slide) -> bool:
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


def _textbox(slide, text, left, top, width, height, size=18, color=None, bold=False):
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
```

---

FILE 8: sub_agents/presentation/server.py

Copy sub_agents/pov/server.py. Adapt:
- agent_name = "presentation"
- AgentCard: inputs=["task", "customer_name", "poc_name"], required=["task", "customer_name"]
- Port 8090 from config.yaml
- After LLM returns JSON spec, call render_oci_powerpoint.render(spec, tmp_path),
  read bytes, return base64-encoded in A2AResponse.result

```python
import base64, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sub_agents.presentation.scripts import render_oci_powerpoint

async def handle(request):
    # ... standard LLM call to get JSON spec from system_prompt ...
    try:
        spec = json.loads(llm_response)
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as f:
            tmp_path = f.name
        render_oci_powerpoint.render(spec, tmp_path)
        pptx_bytes = Path(tmp_path).read_bytes()
        os.unlink(tmp_path)
        return A2AResponse(result=base64.b64encode(pptx_bytes).decode(), status="ok")
    except Exception as exc:
        return A2AResponse(result=str(exc), status="error")
```

---

FILE 9: agent/tools/presentation.py

```python
"""Forge tool handler for generate_presentation. Synthesizes artifacts before calling sub-agent."""
import base64
import json as _json
import logging

from agent import sub_agent_client
from skillforge.types import ToolResult

logger = logging.getLogger(__name__)


class PresentationHandler:
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store       = store
        self._customer_id = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        dc          = getattr(memory, "decision_context", {}) or {}
        poc_option  = args.get("poc_option") or dc.get("poc_recommendation") or {}
        poc_name    = poc_option.get("option_name") or poc_option.get("poc_name") or "OCI POC"

        # ── Synthesize actual artifact content ─────────────────────────────
        bom_content      = await self._load_artifact(dc.get("bom_artifact_key"))
        research_content = await self._load_artifact(dc.get("research_artifact_key"))
        pov_content      = await self._load_artifact(dc.get("pov_artifact_key"))

        task = (
            f"Generate a 7-slide client POC deck.\n\n"
            f"Customer: {self._customer_name}\n"
            f"POC: {poc_name}\n"
            f"OCI Services: {', '.join(poc_option.get('oci_services', []))}\n"
            f"Wow moment: {poc_option.get('wow_moment', '')}\n"
            f"Success criteria: {poc_option.get('success_criteria', poc_option.get('wow_moment', ''))}\n\n"
        )

        if pov_content:
            task += f"[From POV — use for Slide 2 customer challenge]\n{pov_content[:800]}\n\n"
        elif dc.get("pain_statement"):
            task += f"[Customer pain for Slide 2]\n{dc['pain_statement']}\n\n"

        if bom_content:
            task += f"[From BOM — use exact numbers for Slide 5]\n{bom_content[:600]}\n\n"

        if research_content:
            task += f"[From research — use for Slide 6 Why OCI]\n{research_content[:600]}\n\n"

        if poc_option.get("security_highlights"):
            task += f"[Security highlights for Slide 6]\n{poc_option['security_highlights']}\n\n"

        response = await sub_agent_client.call_sub_agent(
            "presentation",
            task=task,
            engagement_context={
                "poc_name":      poc_name,
                "customer_name": self._customer_name,
            },
            trace_id=trace_id,
        )

        if response.status != "ok":
            return ToolResult(status="error", summary=f"Presentation failed: {response.result[:200]}")

        try:
            pptx_bytes = base64.b64decode(response.result)
        except Exception as exc:
            return ToolResult(status="error", summary=f"Failed to decode PPTX: {exc}")

        key = f"presentation/{self._customer_id}/v1.pptx"
        await self._store.save_doc(key, pptx_bytes)

        return ToolResult(
            status="ok",
            summary=f"PowerPoint deck generated: {poc_name} ({len(pptx_bytes):,} bytes)",
            artifact_key=key,
        )

    async def _load_artifact(self, artifact_key: str | None) -> str:
        """Load artifact content from document store. Returns empty string on any failure."""
        if not artifact_key:
            return ""
        try:
            content = await self._store.load_doc(artifact_key)
            return content if isinstance(content, str) else content.decode("utf-8", errors="replace")
        except Exception:
            return ""
```

---

CHANGE 10: agent/archie_wiring.py

Add import: from agent.tools.presentation import PresentationHandler

After generate_waf (or generate_poc_plan after p55c), register:

```python
forge.register_tool(
    "generate_presentation",
    PresentationHandler(store=store, customer_id=customer_id, customer_name=customer_name),
    description=(
        "Generate a 7-slide Oracle-standard PowerPoint deck synthesizing research, BOM, "
        "and diagram artifacts into a coherent customer story. Uses Oracle OCI icon stencils. "
        "Typically called as part of the POC artifact fan-out after generate_poc_plan(action='confirm')."
    ),
    args={"poc_option": ArgSchema(
        description="The confirmed POC option dict (injected automatically in fan-out mode).",
        type="object",
        required=False,
    )},
    memory_contract=True,
    critique_enabled=True,
    requires_hat="oci_presentation_writer",
)
```

CHANGE 11: drawing_agent_server.py

In the /download endpoint, add PPTX content-type before the default return:

```python
if artifact_key.endswith(".pptx"):
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{artifact_key.split("/")[-1]}"'},
    )
```

CHANGE 12: config.yaml — add:
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
  print('PASS: oci_presentation_writer discovered')
  "

  python3.11 -c "
  # Verify the handler loads artifact content (synthesis)
  import sys, inspect; sys.path.insert(0, '.')
  from agent.tools.presentation import PresentationHandler
  src = inspect.getsource(PresentationHandler.__call__)
  assert '_load_artifact' in src, 'FAIL: artifact content loading missing'
  assert 'bom_content' in src, 'FAIL: BOM synthesis missing'
  assert 'pov_content' in src, 'FAIL: POV synthesis missing'
  print('PASS: PresentationHandler synthesizes actual artifact content')
  "

  python3.11 -c "
  import sys, zipfile, io, tempfile, os; sys.path.insert(0, '.')
  from sub_agents.presentation.scripts.render_oci_powerpoint import render
  spec = {'slides': [
    {'slide_number':1,'layout':'title','title':'OCI POC for Acme','subtitle':'Solutions Review','presenter_notes':'Open.'},
    {'slide_number':2,'layout':'bullets','title':'Acme faces $2M infrastructure cost','bullets':['RAC on-prem, $2.1M/yr','EOL in 18 months'],'presenter_notes':'Confirm resonates.'},
    {'slide_number':3,'layout':'architecture','title':'Oracle RAC migrates to ADB-Dedicated','oci_services':[{'name':'Autonomous Database','icon':'OCI_Autonomous_Database'}],'topology_description':'Single AD, us-chicago-1','presenter_notes':'Walk topology.'},
    {'slide_number':4,'layout':'services','title':'Key OCI Services','services':[{'name':'ADB-D','description':'Managed Exadata'}],'presenter_notes':''},
    {'slide_number':5,'layout':'table','title':'Cost: \$644/mo vs \$175K/yr','rows':[{'service':'ADB (2 ECPU)','qty':'1','monthly_cost':'\$400'}],'total':'\$644/mo','presenter_notes':'Use exact numbers.'},
    {'slide_number':6,'layout':'bullets','title':'Why OCI: Real Exadata, not emulation','bullets':['ADB-D runs on Exadata hardware'],'presenter_notes':''},
    {'slide_number':7,'layout':'next_steps','title':'Next: Migrate test DB in 4 hours','bullets':['Pre-provision ADB-D'],'cta':'Run Acme AR query — show delta live','presenter_notes':'Confirm criteria.'},
  ]}
  with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as f:
      tmp = f.name
  render(spec, tmp)
  assert os.path.getsize(tmp) > 1000
  assert zipfile.is_zipfile(tmp)
  from pptx import Presentation as P
  prs = P(tmp)
  assert len(prs.slides) == 7, f'Expected 7, got {len(prs.slides)}'
  os.unlink(tmp)
  print('PASS: render produces valid 7-slide PPTX')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Run Order

```
p55a (background jobs)      ─┐
p55b (poc strategist hat)   ─┤── independent, run in parallel
p55e (presentation)         ─┘
p55c (poc strategist tool)  ── after p55b (needs hat to exist)
p55d (system prompt)        ── after p55b + p55c
```

## Critical Files

| File | Task | Change |
|---|---|---|
| `skillforge/forge.py` | p55a | Add `run_turn_background()` |
| `drawing_agent_server.py` | p55a, p55e | Background endpoint + PPTX content-type |
| `agent/notifications.py` | p55a | Implement Telegram (replace TODO stub) |
| `config.yaml` | p55a, p55c, p55e | Telegram + 2 new sub-agent URLs |
| `ui/src/components/ChatInterface.tsx` | p55a | Background mode toggle + poll |
| `agent/hats/oci_poc_strategist.md` | p55b | New hat — expert POC thinking lens |
| `sub_agents/poc_strategist/` | p55c | New sub-agent (4 files) |
| `agent/tools/specialists.py` | p55c | `PocStrategistHandler` (explore + confirm modes) |
| `agent/archie_wiring.py` | p55c, p55d, p55e | 2 new tools + POC sequencing rules |
| `agent/hats/oci_presentation_writer.md` | p55e | New hat — synthesis pre-action |
| `sub_agents/presentation/` | p55e | New sub-agent (6 files + assets/) |
| `agent/tools/presentation.py` | p55e | `PresentationHandler` (artifact synthesis) |
| `requirements.txt` | p55e | Add `python-pptx>=1.0.2` |

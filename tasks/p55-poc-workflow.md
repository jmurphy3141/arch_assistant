# p55 — POC Workflow: Strategy, Background Execution, Parallel Artifacts, PowerPoint

**Revision 3 — Expert reasoning depth + reasoning loop integration**

---

## Vision Alignment

Archie should feel like a **proactive senior OCI Solutions Architect** who has run 50+ successful POCs.
When wearing the POC Strategist hat, Archie does not present options neutrally — it reads the room,
names the archetype, calls out the risks, and gives a strong recommendation backed by specific evidence.

**Three principles:**
- **Hats are thinking lenses.** The pre-action is where expertise lives. Sub-agents execute what the
  hat prescribes.
- **Prompt-first.** Semantic judgment (when to confirm, how to present, what to recommend) belongs in
  the system prompt and hats — not Python.
- **Forge stays manager-agnostic.** One new method (`run_turn_background`). Everything else is Archie.

---

## Gap Analysis

| Capability | Status | Root cause |
|---|---|---|
| "What should we build?" | Missing | No POC archetype reasoning — only artifact generation |
| Expert POC risk reasoning | Missing | `infra_tech_research` covers service selection, not deal strategy |
| Background execution | Missing | SSE requires open connection for full turn |
| Parallel artifact generation | Blocked | Fan-out path exists in Forge — needs triggering |
| PowerPoint as synthesis | Missing | No PPTX; no artifact-content synthesis |
| Telegram notification | Stub only | `notifications.py` has TODO, never implemented |
| Natural background UX | Missing | 202 + job_id is not a chat experience |

---

## What Changes (Forge vs. Archie Boundary)

| Layer | Change | Why |
|---|---|---|
| **Forge** (`skillforge/`) | `run_turn_background()` — one new method | Forge owns turn execution mechanics |
| **Archie** (`agent/`) | POC Strategist hat + Presentation hat + system prompt | All OCI/SE domain content |
| **Archie** (`sub_agents/`) | `poc_strategist`, `presentation` sub-agents | Execution specialists |
| **Server** (`drawing_agent_server.py`) | `/api/chat/background` + PPTX content-type | Infrastructure serving Archie |

---

## Success Criteria

1. **SE gives rough context:** "Customer is a financial services firm, Oracle RAC on-prem, CFO flagged
   $2M bill, exec review in 3 weeks." Archie responds with 3 scored POC options, recommends the ADB
   migration citing "$2M" and "3 weeks" specifically, names the wow moment and 2 risks with mitigations.

2. **SE kicks off during a meeting:** Archie replies instantly: "On it — exploring DB migration, AI/ML,
   and cost angles for Acme Financial in parallel. 2–3 minutes. Telegram when ready. Job: `abc123`."
   SE receives a Telegram with the top option name, score, wow moment, and a call-to-action.

3. **SE confirms:** "Go with the DB migration." Archie calls `generate_poc_plan(action="confirm")` via
   the LLM — no regex — and fans out all 5 artifacts simultaneously. All done in ~90 seconds.

4. **SE downloads the deck.** Slide 5 shows actual BOM line items. Slide 2 uses language from the POV.
   Slide 7 cites the wow moment verbatim. No placeholder text anywhere.

---

## POC Strategist Hat: The Expert Thinking Engine

This is the most important artifact in p55. The hat is what makes Archie feel senior — not the Python.

### The 30-Second SE Read: Pattern Recognition

A senior SE can read the situation from minimal signals. The hat must encode these heuristics explicitly
so the pre-action reasoning pattern-matches immediately:

| Signal combination | Archetype | First move |
|---|---|---|
| "Oracle RAC" + "cost" + "CFO" | **DB Migration** | ADB-D migration POC. 85% likely to be right. |
| "MySQL" + "analytics" + "slow queries" | **HeatWave** | Same query, 10-100× faster. 3h build. |
| "K8s on-prem" + "DevOps team" + "deployment" | **OKE Modernization** | Speed-of-deployment proof. |
| "AWS" + "$XM bill" + "CFO approved migration" | **Cost Optimization** | Need current bill breakdown first. |
| "AI" + "GPU" + "new initiative" | **AI/ML on OCI** | Probe for actual workload before scoping. |
| "compliance" + "audit" + "DR" | **Disaster Recovery** | Risk audience, not technical audience. |
| "HIPAA" + "PHI" + "healthcare" | **DB Migration or DR** | Lead with BAA + data residency controls. |

The hat's **Core Principles** section must include this table and instruct the expert to name the
archetype in the first line of the Expert Assessment — before scoring angles.

### Deal Stage Reading from Conversational Signals

The hat should infer deal stage from signals, not rely on an explicit label in memory:

| What they say | Inferred stage | Hat response |
|---|---|---|
| "We're evaluating options" / "We want to see what OCI can do" | Discovery | Offer 3 options, let them pick the angle |
| "We've narrowed to OCI and AWS" / "We need to make a decision" | Evaluation | One focused POC, maximum pain relevance |
| "Our board approved OCI" / "We need to show this works" | Decision | Risk-reduction POC — show the migration path, what happens if it fails |
| "Our SE was supposed to..." / "We already started a POC" | In progress | Surface memory conflict, don't restart |
| "We need this done in X weeks" | Timeline pressure | Simplify scope — 4h POC beats 8h POC every time |

If deal stage is not inferable, default to **Evaluation** and surface the assumption explicitly:
`Assumption: deal stage = "evaluation" (not stated — confirm if incorrect).`

### Risk Anticipation by Archetype

The hat must surface the 2 most likely failure modes per archetype — not generic risks:

**Oracle DB Migration:**
- Stored procedures incompatible with ADB → *Mitigation: Run Oracle DB Compatibility Checker 48h before.*
- Data volume underestimated — test schema has referential integrity constraints → *Scope to 1-2 critical tables only.*

**OKE Modernization:**
- Customer app has stateful pods → *Show StatefulSets, not the stateless hello-world.*
- Base container images not pre-pulled → *Pre-pull before demo — first pull during the call is not a wow moment.*

**HeatWave Acceleration:**
- Customer doesn't have MySQL (uses PostgreSQL or Oracle) → *Wrong archetype — pivot to analytics or DB migration.*
- Query choice doesn't show HeatWave advantage → *Use customer's actual slow analytic query, not a benchmark.*

**Cost Optimization:**
- "Cost optimization" is too abstract without a current bill → *Get the customer's cloud bill line items before scoping.*
- Comparison to on-prem is hard without license cost data → *Ask for current Oracle support cost.*

**Disaster Recovery:**
- Audience is business stakeholders, not IT → *Translate RTO/RPO to business impact: "your transactions resume in X minutes."*
- Failover demo requires pre-provisioned secondary region → *Pre-provision 24h before, confirm cross-region connectivity.*

### Success Criteria That Close Deals

Generic success criteria do not close deals. The hat must push for measurable, customer-specific criteria:

❌ Generic: "Demonstrate OCI performance."
✅ Closing: "Customer's AR reconciliation query completes in under 5 seconds on ADB-D vs. 47 seconds on RAC."

❌ Generic: "Show cost savings."
✅ Closing: "OCI BOM for equivalent workload = $644/mo vs. $175K/yr on-prem (license + hardware)."

**Template the hat should produce:**
> "[Customer's specific workload] completes [metric: time/cost/availability] [target value] on OCI
> vs. [current value] on [current platform], demonstrable in a live session."

### Proactive Recommendations

The hat must surface things the SE hasn't asked about — this is what makes Archie feel senior:

- **Before the demo:** "Run Oracle DB Compatibility Checker 48h before scoping — stored procedures are the silent POC killer."
- **Region availability:** "Confirm ADB-D shape is available in the target region before committing to the demo date. Not all regions have all shapes."
- **Use customer data:** "Ask for a sanitized subset of the customer's actual workload data — demos with customer data close more deals than benchmark data."
- **Pre-provision:** "Pre-provision the ADB instance the day before. Provisioning takes 45 minutes. Showing a progress bar during the customer call is not a wow moment."
- **Audience alignment:** "Confirm who will be in the room. A performance demo for a CFO audience lands differently than for a DBA audience — have the cost calculator open, not the query plan."

The Pre-Action Checklist section of the hat must end with a `PROACTIVE FLAG:` item — one specific thing
the SE should do before the demo that they haven't asked about.

---

## How the Reasoning Loop Works for POC Planning

The hat plugs into Forge's existing Step 3 → Step 4 → Step 5 reasoning loop. No changes to `forge.py`
beyond `run_turn_background()`.

### Step 3: Planning (before hat activates)

When Archie identifies a POC planning request, Step 3 planning resolves:
1. Is memory sufficient? `pain_statement` and `current_platform` are required; if absent, surface `NEEDS_CLARIFICATION` before the hat activates.
2. Explore or confirm mode? If `poc_options` already in memory and user expressed selection intent → `confirm`. Otherwise → `explore`.
3. Customer archetype: what does the 30-second read say?
4. What is the single highest-priority missing input that would change the recommendation?
5. What is the expected build time for the likely archetype?

### Step 4: Expert Pre-Action (wearing the hat)

The expert pre-action is the heart of POC quality. Format:

```
KNOWN FACTS:
  pain_statement: [from memory]
  current_platform: [from memory]
  customer_industry: [from memory, or "enterprise technology (assumed)"]
  deal_stage: [from memory or inferred, with evidence]
  timeline: [from memory, or "flexible (assumed)"]
  budget_signal: [from memory, or "not stated"]
  competitive_context: [from memory, or "none stated"]

GAPS:
  - [field]: [value assumed] — [what evidence would change this]

EXPERT ASSESSMENT (POC STRATEGY):

CUSTOMER ARCHETYPE: [Name it. Justify with ≥2 evidence points from KNOWN FACTS.]

DEAL STAGE READING: [Infer from conversational signals. State evidence. State assumed stage.]

POC SELECTION REASONING:
  - migration_modernization: [Why strong or weak for THIS customer — score 1-10 with justification]
  - performance_scale_ai:    [Same treatment]
  - cost_optimization_tco:   [Same treatment]
  RECOMMENDED: [Angle] because [cite ≥2 specific facts from KNOWN FACTS]

SUCCESS PATTERN for [recommended archetype]:
  Step 1: [Provision X — show OCI speed]
  Step 2: [Core proof — directly proves the pain]
  Step 3: [Cost/compliance moment]
  Step 4: [Call to action setup]

WOW MOMENT: [30-second action. Specific: "run customer's own [workload] — show [metric] vs. [current]."]

TOP POC RISKS:
  - Risk 1: [Specific failure mode for this archetype + this customer] — Mitigation: [concrete action]
  - Risk 2: [Second risk] — Mitigation: [concrete action]

COMPETITIVE CONTEXT: [If competitor named: OCI-specific differentiator this POC proves. Be specific.]

PROACTIVE FLAG: [One thing SE should do before the demo that they haven't thought of.]

[SUB-AGENT TASK — migration_modernization]
Customer: [name]
Industry: [industry]
Deal stage: [stage with evidence]
Angle: migration_modernization
Customer archetype: [archetype name]
Pain: [verbatim pain_statement]
Platform: [current_platform]
Timeline: [timeline]
Competitive context: [context or "none stated"]
Success pattern: [4 steps]
Wow moment: [verbatim from above]
Top risks for this angle: [risk 1, risk 2]
Pre-demo preparation required: [specific steps]
[/SUB-AGENT TASK]

[SUB-AGENT TASK — performance_scale_ai]
... [same structure] ...
[/SUB-AGENT TASK]

[SUB-AGENT TASK — cost_optimization_tco]
... [same structure] ...
[/SUB-AGENT TASK]
```

### Step 5: Post-Review Quality Gate

After `generate_poc_plan` returns the 3-option response, the expert post-review checks:

| Check | Pass condition | Failure correction prompt |
|---|---|---|
| 3 options present | One per angle | "CORRECTION: Only {n} options returned. Re-run missing angle: {angle}." |
| Rationale specificity | Cites ≥2 facts from KNOWN FACTS | "CORRECTION: Rationale is generic. Available facts: {pain}, {timeline}, {budget}. Rewrite citing at least 2." |
| WOW MOMENT specificity | ≤2 sentences, names customer workload, includes a metric | "CORRECTION: Wow moment must be a 30-second demo action with a metric. Template: 'Run [customer]'s [workload] — show [delta] vs. [current state].'" |
| Pre-demo checklist | ≥2 concrete items | "CORRECTION: Pre-demo checklist is missing or generic. Name 2 specific preparation steps for an [archetype] POC." |
| Executability | All options ≤ 8 hours | "CORRECTION: {option} shows {hours}h — exceeds SE demo window. Scope down or replace with a simpler angle." |
| Option name specificity | Contains customer name or workload | "CORRECTION: Option name is generic. Include customer name and workload: '[Customer] [workload] → [target].'" |
| Artifact key | Present | "CORRECTION: artifact_key missing — plan was not saved to document_store." |

---

## Prompt Improvements

### `_EXPERT_IDENTITY` patch (Task p55d)

Add to the existing `_EXPERT_IDENTITY` block in `agent/archie_wiring.py`:

```
You recognize workload patterns immediately from minimal signals:
- "Oracle RAC" + cost pain → ADB migration is the likely POC (85% win rate pattern)
- "MySQL" + analytics → HeatWave shows 10-100× improvement with 3h build time
- "K8s on-prem" + DevOps team → OKE modernization, speed-of-deployment proof
- CFO-driven evaluation → every slide needs a cost number, not just a feature

You anticipate what kills POCs before the SE asks:
- No agreed success criteria before the demo
- Audience mismatch (performance demo for business stakeholders)
- Build time underestimated, SE scrambles during the customer call
- Pre-provisioning skipped — provisioning progress bars are not wow moments

You give specific proactive recommendations. Not "plan carefully." Instead:
"Run Oracle DB Compatibility Checker 48h before — stored procedures are the silent POC killer."
```

### `_TOOL_SEQUENCING_RULES` addition (Task p55d)

```
### POC Planning Workflow

When the SE needs to know what to build for a customer:

1. Call generate_poc_plan (default: action="explore"). Runs 3 parallel evaluations.
   Returns ranked options with relevance score, build time, wow moment, pre-demo checklist.

2. Present options clearly. For each: name, relevance score (X/10), build time (Xh),
   wow moment, top risks. Give your recommendation with rationale citing ≥2 specific
   customer facts. End with: "Which option would you like to proceed with?"

3. Wait for confirmation. When the user selects — by number ("option 1"), by name
   ("the DB migration"), by description ("the cost one"), or by affirmation ("that one",
   "let's do it") — extract confirmed_option_name from the options list and call:
     generate_poc_plan(action="confirm", confirmed_option_name="[exact option_name]")

4. The confirm call fans out all 5 artifacts simultaneously. When all complete, present
   as a package: "POC kit for [name] is ready: architecture diagram, BOM (~$X/mo),
   JEP execution plan, Terraform scripts, and client deck. [Download links.]"

5. Do NOT generate artifacts before the user confirms an option.
6. Do NOT call generate_poc_plan(action="explore") again after confirmation.
7. If user says "try option 2 instead", call confirm with the option 2 name.
8. If ambiguous, ask once: "Which option — the [name1] (Xh, Y/10) or the [name2]?"
```

---

## Background Job UX

### The 202 Acknowledgment (templated, no LLM call)

The acknowledgment varies by job type — pre-generated from turn context:

**POC exploration:**
> "On it — exploring 3 POC angles for **{customer_name}** in parallel: DB migration modernization,
> performance/AI, and cost optimization. Typically 2–3 minutes. I'll send a Telegram notification
> when the plan is ready, or ask me anything else in the meantime. Job: `{job_id}`"

**Artifact fan-out:**
> "POC confirmed: **{poc_name}**. Generating all 5 artifacts simultaneously — diagram, BOM, JEP,
> Terraform, and client deck. Usually under 90 seconds. I'll notify you on Telegram. Job: `{job_id}`"

**Single artifact:**
> "Generating {artifact_type} for **{customer_name}** in the background. Usually under 60 seconds.
> Job: `{job_id}`"

### Telegram Notification Content

**POC plan complete:**
```
✅ *Archie: POC plan ready* for {customer_name}
Recommended: {poc_name} ({relevance}/10 relevance, {hours}h build)
Wow: {wow_moment}
3 options explored. Reply 'confirm {option_name}' in chat to generate all artifacts.
```

**Artifact fan-out complete:**
```
✅ *Archie: POC kit ready* for {customer_name}
{poc_name} — all 5 artifacts generated
• Architecture diagram
• BOM: ~{monthly_cost}/mo
• JEP execution plan
• Terraform scripts
• Client PowerPoint deck
Open chat to review and download.
```

### Easy Resumption

When the user returns to chat after a background job completes:
- The completed result is retrieved from job history and memory
- If the user asks "what happened with Acme?" or "did the POC plan finish?" — Archie presents
  the result naturally, not "please check /api/job/{id}"
- The conversation history reads as if Archie was working while the SE was away

The `/api/chat/background` 202 response must include the `acknowledgment` string so the UI can
append it to the chat thread immediately — the SE sees Archie's reply before leaving the meeting.

### UI Behavior

- "Background" toggle in the chat input footer (clock icon)
- When active: POST to `/api/chat/background` instead of opening SSE stream
- On 202: append `acknowledgment` text to chat as an Archie message (same bubble style)
- Show subtle working indicator (spinner + job_id) in the thread
- Poll `GET /api/job/{job_id}` every 5 seconds
- On complete: append `result.reply` to chat, remove spinner
- On error: show error message in chat bubble, remove spinner

---

## Presentation: Research → BOM → Diagram → Executive Deck

### Synthesis Pipeline

The `PresentationHandler` must load actual artifact content before calling the sub-agent.
Artifact keys come from `memory.decision_context`:

| Source | Key in memory | Extract | Use in deck |
|---|---|---|---|
| BOM | `bom_artifact_key` | `monthly_total`, top 5 line items | Slide 5 — exact numbers |
| Research | `research_artifact_key` | `recommendation_rationale`, `risk_register`, `competitive_differentiators` | Slide 6 — Why OCI |
| POV | `pov_artifact_key` | `executive_summary` (first 2 paragraphs) | Slide 2 — Customer Challenge |
| Diagram | `diagram_artifact_key` | Service names from drawio XML (or BOM's `oci_services_required`) | Slide 3 — Architecture |
| POC recommendation | `memory.decision_context.poc_recommendation` | `poc_name`, `wow_moment`, `success_criteria`, `demo_script_summary` | Slides 1, 7 |

The sub-agent receives a fully hydrated `[PRESENTATION BRIEF]` — not artifact keys.
This is the quality difference between a template deck and a customer story.

### Story Arc

Every slide must advance the narrative. The arc is:

1. **Title** — Context: who we are, what we're showing, why today
2. **Challenge** — Empathy: mirror the customer's situation back to them. They should nod at every bullet.
3. **Architecture** — Solution: clean topology, OCI icons, no acronym soup
4. **Services** — Value: what each service does *for them* — not what OCI services exist generically
5. **Cost** — The number: compare to their current spend. This slide closes rooms.
6. **Why OCI** — Differentiator: address their specific concern (security? performance? support?)
7. **Next Steps** — Action: concrete, time-boxed, starts with the wow moment

### Slide-by-Slide Quality Bar

- **Slide 2:** Every bullet references the customer's specific situation (name, workload, or spend figure)
- **Slide 5:** No "TBD" or "estimated" — if BOM was generated, use exact line items and `monthly_total`
- **Slide 6:** Must name the OCI differentiator vs. the customer's current platform (not generic "OCI is great")
- **Slide 7:** Must include `wow_moment` as a demo action, not a feature description. Include success criteria.
- **All slides:** `presenter_notes` includes one expected objection and a specific response

### Presenter Notes Quality Standard

❌ Generic: "Explain the architecture diagram."

✅ Senior SE: "Walk from left to right: on-prem DRG → VCN → ADB-D subnet. When asked 'how long does
provisioning take?', say '20 minutes — we pre-provisioned this one.' Common objection: 'we're Oracle-licensed'
→ 'ADB-D preserves your license investment — this is not a re-license conversation.'"

Every slide's presenter notes must include:
- What to say when presenting this slide (1-2 sentences)
- One expected objection and a specific response

---

## p55 Task Overview

| Task | Description | Layer | Effort | Depends on |
|---|---|---|---|---|
| **p55a** | Background job for chat turns + Telegram | Forge + Server | 1 day | — |
| **p55b** | POC Strategist hat — expert thinking lens | Archie/Hats | 1 day | — |
| **p55c** | POC Strategist tool — sub-agent + handler + wiring | Archie/Tools | 1 day | p55b |
| **p55d** | Archie system prompt: `_EXPERT_IDENTITY` + POC sequencing | Archie/Prompt | 0.5 days | p55b, p55c |
| **p55e** | Presentation — hat + synthesis handler + sub-agent + renderer | Archie | 1.5 days | — |

p55a, p55b, p55e are independent — run in parallel.
p55c requires p55b. p55d requires p55b and p55c.

---

## Task p55a — Background chat job support + Telegram notification

```
Context: The /api/chat/stream SSE connection holds open for the full turn.
SEs need to kick off POC generation during a meeting without holding a connection.
When done, they get a Telegram notification with key content (not just "complete").

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

Add one method to the Forge class (after run_turn, before any private methods).
Domain-agnostic — no Archie logic, no OCI references, no artifact handling:

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
    on_complete(TurnResult) called on success.
    on_error(Exception) called on failure.
    No SSE — caller manages job lifecycle via callbacks.
    """
    try:
        result = await self.run_turn(message, history, context)
        await on_complete(result)
    except Exception as exc:
        await on_error(exc)
```

---

CHANGE 2: drawing_agent_server.py

Add the background endpoint immediately after the /api/chat/stream definition.
Adapt _resolve_customer_name, _build_archie_session, and session.load_history()
to match whatever the existing /api/chat/stream endpoint uses — do not invent new patterns.

```python
@app.post("/api/chat/background", status_code=202)
async def chat_background(request: ChatRequest):
    job_id       = _new_job()
    customer_id  = request.client_id or "default"
    customer_name = await _resolve_customer_name(customer_id)

    acknowledgment = (
        f"On it — starting analysis for **{customer_name}** in the background. "
        f"Usually takes 2–3 minutes. I'll send a Telegram notification when ready. "
        f"Job: `{job_id}`"
    )

    async def on_complete(result) -> None:
        _complete_job(job_id, {
            "reply":    result.reply,
            "artifacts": result.artifacts,
        })
        preview = (result.reply or "")[:300]
        keys    = list((result.artifacts or {}).values())[:3]
        await _notify_background_complete(customer_id, customer_name, preview, keys)

    async def on_error(exc: Exception) -> None:
        _fail_job(job_id, str(exc))

    store       = getattr(app.state, "object_store", None)
    text_runner = getattr(app.state, "text_runner",  None)
    session     = await _build_archie_session(customer_id, store, text_runner)

    asyncio.create_task(
        session.forge.run_turn_background(
            message    = request.message,
            history    = session.load_history(),
            context    = session.context,
            on_complete = on_complete,
            on_error   = on_error,
        )
    )
    return {"job_id": job_id, "status": "pending", "acknowledgment": acknowledgment}


async def _notify_background_complete(
    customer_id: str, customer_name: str, reply_preview: str, artifact_keys: list
) -> None:
    artifacts_note = ""
    if artifact_keys:
        names = ", ".join(k.split("/")[-1] for k in artifact_keys)
        artifacts_note = f"\nArtifacts: {names}"
    text = (
        f"✅ *Archie: work complete* for {customer_name}\n"
        f"{reply_preview}{artifacts_note}"
    )
    from agent.notifications import notify
    notify("background_complete", customer_id, detail=text)
```

---

CHANGE 3: agent/notifications.py

Replace the `_send` function body (lines ~62-75):

```python
def _send(event: str, customer_id: str, detail: str) -> None:
    logger.info("NOTIFY event=%s customer_id=%s detail=%r", event, customer_id, detail)
    _fire_telegram(detail or f"[{event}] {customer_id}")


def _fire_telegram(text: str) -> None:
    """Schedule Telegram send without blocking. Safe to call from sync context."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send_telegram(text))
    except RuntimeError:
        pass


async def _send_telegram(text: str) -> None:
    """Fire-and-forget — all failures are swallowed."""
    import os
    try:
        import httpx, yaml
        cfg  = yaml.safe_load(open("config.yaml", encoding="utf-8"))
        tg   = cfg.get("telegram", {})
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
        pass
```

---

CHANGE 4: config.yaml — add after sub_agents section:

```yaml
telegram:
  enabled: false
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  chat_id_env:   "TELEGRAM_CHAT_ID"
```

---

CHANGE 5: ui/src/components/ChatInterface.tsx

- Add "Background" toggle button in chat input footer (clock icon)
- When active: POST /api/chat/background instead of opening SSE stream
- On 202: append acknowledgment to chat as an Archie message bubble
- Show subtle working indicator (spinner + "working in background..." label + job_id)
- Poll GET /api/job/{job_id} every 5 seconds
- When status == "complete": append result.reply to chat, remove indicator
- When status == "error": show error in chat bubble, remove indicator

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
  tg  = cfg.get('telegram', {})
  for k in ('enabled', 'bot_token_env', 'chat_id_env'):
      assert k in tg, f'FAIL: telegram.{k} missing'
  print('PASS: telegram config present')
  "

  python3.11 -c "
  import sys, inspect; sys.path.insert(0, '.')
  from agent.notifications import _send_telegram, _fire_telegram
  src = inspect.getsource(_send_telegram)
  assert 'api.telegram.org' in src, 'FAIL: Telegram URL missing'
  assert 'enabled' in src, 'FAIL: enabled check missing'
  print('PASS: Telegram implementation correct')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55b — POC Strategist hat (expert thinking lens)

```
Context: This task is ONLY the hat file. No Python code.
The hat is the most important artifact in p55. Read the full Expert Thinking Engine
specification in this plan. Read agent/hats/oci_bom_expert.md and
agent/hats/infra_tech_research.md as format references.

IMPORTANT: Branch from origin/main. Independent of all other p55 tasks.

  git fetch origin
  git checkout -b claude/p55b origin/main

---

FILE: agent/hats/oci_poc_strategist.md

YAML frontmatter:
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
    budget signals, and competitive context. The goal is to recommend the POC
    most likely to close this specific deal.
coordination:
  parallel_with:
    - "infra_tech_research"
  suggested_next_hat: "diagram_for_oci"
  handoff_message: >
    POC plan delivered. When the SE confirms an option, fan out to
    diagram + BOM + JEP + Terraform + presentation simultaneously.
---
```

### Required sections (match oci_bom_expert.md format exactly):

**## OCI POC Strategist**
Intro: "You are a senior OCI Solutions Architect who has run 50+ successful POCs.
You recognize workload patterns immediately, anticipate what kills POCs before they happen,
and give specific opinionated recommendations backed by evidence from this customer's context.
You do not present options neutrally — you recommend, justify with tradeoffs, and surface risks."

**## Core Principles**
Include ALL of the following — verbatim if helpful, but in the hat's voice:
1. Lead with the deal, not the demo (pain-first)
2. Name the archetype before scoring angles (pattern recognition table — include the full table)
3. Deal stage changes everything (include the deal stage reading table)
4. Industry-specific compliance wins (FS, Healthcare, Retail, Manufacturing, Public Sector overlays)
5. Anticipate what kills POCs (include the full risk-per-archetype list)
6. Specificity about the wow moment (30-second rule — include the good/bad examples)
7. Success criteria that close deals (include the template)
8. The sub-agent brief must be complete (sub-agent has no other context)
9. Proactive recommendations (list the specific examples)

**## POC Archetypes**
Include the full 6-row archetype table from this plan's Expert Thinking Engine section.

**## Quality Bar**
The 7-item post-review checklist from the Post-Review Quality Gate table (see above).
Each item must include the correction prompt string to inject on failure.

**## Output Contract**
JSON structure with poc_options[] and recommendation. Include the full example JSON from this plan.

**## Critic Evaluation Guidance**
Critic must verify: rationale is customer-specific (not generic), wow moment has a metric,
pre_demo_checklist has ≥2 items, all executability_hours ≤ 8, option names include
customer name or workload description.

**## Failure Questions**
What questions should the critic ask? Examples:
- "Does the rationale cite facts from this specific customer's context, or could it apply to any customer?"
- "Is the wow moment a 30-second demo action or a vague capability description?"
- "Is the pre_demo_checklist specific or generic?"

**## Activation & Drop**
Activate: when generate_poc_plan is called or user asks for POC direction.
Drop: when user confirms an option (hat's work is done; wiring triggers fan-out).

**## Pre-Action Checklist**
Must follow the exact format from the "Step 4: Expert Pre-Action" section of this plan:
KNOWN FACTS → GAPS → EXPERT ASSESSMENT (with all 9 sub-sections) → 3 SUB-AGENT TASK blocks.

Required-field gates (before expert assessment):
- pain_statement absent → NEEDS_CLARIFICATION: "What is the customer's primary pain
  (cost, performance, risk, compliance, time-to-market)?"
- current_platform absent → NEEDS_CLARIFICATION: "What platform is the customer currently
  running on (on-prem Oracle, AWS, Azure, bare metal)?"
- customer_industry absent → default "enterprise technology" — document assumption
- deal_stage absent → infer from conversational signals; default "evaluation" — document assumption
- timeline absent → default "flexible" — document assumption

**## Post-Action Review**
Run all 7 quality bar checks from the Quality Bar section.
For each check that fails, inject the correction prompt from the Quality Bar table.
Decision: all pass → approve for critic. Any fail → iterate with the correction prompt.

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
  checks = [
      ('Lead with the deal',      'deal-first principle'),
      ('archetype',               'archetype pattern matching'),
      ('NEEDS_CLARIFICATION',     'clarification gate'),
      ('SUB-AGENT TASK',          'sub-agent task blocks'),
      ('migration_modernization', 'migration angle'),
      ('performance_scale_ai',    'AI angle'),
      ('cost_optimization_tco',   'cost angle'),
      ('wow_moment',              'wow moment field'),
      ('pre_demo_checklist',      'pre-demo checklist'),
      ('deal_stage',              'deal stage logic'),
      ('CORRECTION',              'correction prompts in post-action'),
      ('PROACTIVE FLAG',          'proactive recommendation'),
  ]
  for content, label in checks:
      assert content in hat, f'FAIL: {label!r} not found — missing {content!r}'
  print('PASS: all critical hat sections present')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Only create agent/hats/oci_poc_strategist.md. No other files.
```

---

## Task p55c — POC Strategist tool (sub-agent + handler + registration)

```
Context: Task p55b created the hat. This task creates the execution layer:
sub-agent, handler, and registration. The handler makes 3 parallel asyncio.gather()
calls — not 1. It supports two modes: explore (3 parallel calls) and confirm (fan-out).
Confirm mode is triggered by the LLM via action="confirm" — no Python regex detection.

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
  model_id: ""
  max_tokens: 2048
  temperature: 0.6
```

---

FILE 3: sub_agents/poc_strategist/system_prompt.md

```markdown
# POC Strategist Sub-Agent

You are the OCI POC strategy analyst for Archie. Given a customer context and a
specific exploration angle, evaluate ONE POC option and return it as structured JSON.

You receive a complete brief from Archie. The brief includes customer archetype,
industry, deal stage, the angle you are evaluating, success pattern, wow moment,
and pre-demo preparation requirements. Use this brief. Do not invent context.

## Your Job

Evaluate the single POC angle specified in your brief. Produce one option with:

- **option_name**: Customer-specific. Include the customer name and workload.
  "Oracle RAC → ADB-Dedicated migration for Acme Financial" not "Database POC."
- **angle**: Exactly one of: migration_modernization / performance_scale_ai / cost_optimization_tco
- **relevance_score**: 1–10. Does this POC directly prove the customer's stated pain?
  10 = if this demo succeeds, there is no reasonable reason to say no.
  5 = demonstrates OCI capability but doesn't directly address the pain.
- **executability_hours**: Integer. Hours to build + demo-ready. Include: provisioning,
  data loading, validation. Do not underestimate. Maximum 8h for a viable POC.
- **cost_effectiveness**: Defensible OCI cost range vs. current spend. Be specific:
  "$640/mo vs. ~$175K/yr."
- **security_highlights**: 2–4 OCI security controls. Use exact service names:
  "OCI Security Zones", "Data Safe", "OCI Vault KMS."
- **oci_services**: List of specific OCI service names. Minimum 3.
- **wow_moment**: One sentence. 30-second demo action with a specific metric.
  Must reference the customer's actual pain or workload.
- **demo_script_summary**: 2–3 sentences. What the SE does step by step to reach the wow moment.
- **pre_demo_checklist**: 2–4 concrete preparation steps the SE must do BEFORE the demo.

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

If the brief is insufficient:
{"type": "needs_input", "reply": "One sentence: what is missing from the brief."}
```

---

FILE 4: sub_agents/poc_strategist/server.py

Copy sub_agents/pov/server.py exactly. Adapt:
- agent_name = "poc_strategist"
- AgentCard: name, description="OCI POC strategy analyst — evaluates one POC angle per call",
  inputs=["task", "angle", "customer_context"], required=["task"]
- Port from config.yaml (8089)

---

CHANGE 5: agent/tools/specialists.py

Add at the bottom (after TechResearchHandler):

```python
class PocStrategistHandler:
    """
    Two modes controlled by the 'action' argument:

    action="explore" (default):
        3 parallel asyncio.gather() calls to poc_strategist sub-agent.
        Returns ToolResult(status="ok") with 3 options + recommendation.

    action="confirm" + confirmed_option_name:
        Looks up the confirmed option from memory.
        Returns ToolResult(status="parallel", parallel_tools=[5 artifact tools])
        — triggers Forge's existing asyncio.gather() fan-out path.
    """
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store       = store
        self._customer_id = customer_id
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
                    clarification="Please generate a POC plan first.",
                )

            option = next(
                (o for o in poc_options
                 if confirmed_name.lower() in o.get("option_name", "").lower()),
                poc_options[0],
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
                            "_user_message": f"Create client PowerPoint for POC: {poc_name}.",
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
                    f"Relevance {rec.get('relevance_score')}/10, "
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
            summary=(
                f"Explored 3 POC options. Recommended: {rec.get('option_name')} "
                f"({rec.get('relevance_score')}/10 relevance, {rec.get('executability_hours')}h build)"
            ),
            artifact_key=key,
            data=payload,
        )
```

---

CHANGE 6: agent/archie_wiring.py

Add import: from agent.tools.specialists import ..., PocStrategistHandler

After generate_waf registration:

```python
forge.register_tool(
    "generate_poc_plan",
    PocStrategistHandler(store=store, customer_id=customer_id, customer_name=customer_name),
    description=(
        "Plan a technical POC. "
        "action='explore' (default): explores 3 parallel POC angles (migration, AI/ML, cost) "
        "and returns ranked options with wow moments, build times, and risk assessments. "
        "action='confirm' + confirmed_option_name: fans out all 5 artifacts in parallel "
        "(diagram, BOM, JEP, Terraform, presentation). "
        "Call action='explore' when SE needs POC direction. "
        "Call action='confirm' after the user selects an option."
    ),
    args={
        "action": ArgSchema(
            description="'explore' to generate 3 options, 'confirm' to start artifact generation.",
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
  assert 'asyncio.gather' in src,              'FAIL: 3 parallel calls missing'
  assert \"action == 'confirm'\" in src or 'action == \"confirm\"' in src, 'FAIL: confirm mode missing'
  assert 'ParallelToolCall' in src,            'FAIL: ParallelToolCall not used'
  assert 'generate_presentation' in src,       'FAIL: presentation not in fan-out'
  print('PASS: explore + confirm modes + parallel fan-out')
  "

  python3.11 -c "
  import sys; sys.path.insert(0, '.')
  from unittest.mock import MagicMock
  from agent.archie_wiring import build_forge
  forge = build_forge(store=MagicMock(), customer_id='test', customer_name='Test',
                      text_runner=MagicMock(), step3_planning=False)
  spec = forge._registry.get('generate_poc_plan')
  assert spec is not None,                        'FAIL: generate_poc_plan not registered'
  assert spec.requires_hat == 'oci_poc_strategist', f'FAIL: wrong hat: {spec.requires_hat}'
  assert spec.memory_contract,                    'FAIL: memory_contract not set'
  assert 'action' in (spec.args or {}),           'FAIL: action arg missing'
  print('PASS: generate_poc_plan registered correctly')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Task p55d — Archie system prompt: _EXPERT_IDENTITY + POC workflow sequencing

```
Context: With hat (p55b) and tool (p55c) in place, Archie needs two prompt changes:
1. _EXPERT_IDENTITY gains POC pattern recognition and risk instinct language.
2. _TOOL_SEQUENCING_RULES gains the POC workflow with confirm/explore distinction.
Prompt-first: the LLM decides when to call action='confirm', not Python.

Depends on: p55b and p55c merged.

IMPORTANT: Branch from origin/main (or from p55c if not yet merged).

  git fetch origin
  git checkout -b claude/p55d origin/main

---

CHANGE 1: agent/archie_wiring.py — append to _EXPERT_IDENTITY

Find the _EXPERT_IDENTITY string and add this block at the end, before the closing triple-quote:

```
You recognize workload patterns immediately from minimal signals:
- "Oracle RAC" + cost pain → ADB migration is the likely POC (high win rate, 4h build)
- "MySQL" + analytics → HeatWave shows 10-100× improvement with 3h build time
- "K8s on-prem" + DevOps team → OKE modernization, speed-of-deployment proof
- CFO-driven evaluation → every recommendation needs a cost number, not just a feature
- "HIPAA" or "PCI" + database → lead with Security Zones and Data Safe before cost

You anticipate what kills POCs before the SE asks:
- No agreed success criteria before the demo starts
- Wrong audience (performance demo for business stakeholders)
- Wow moment buried — happens at step 15, audience attention gone by step 8
- Build time underestimated — SE scrambles during the customer call
- Pre-provisioning skipped — provisioning progress bars are not wow moments

You give specific proactive recommendations, not generic advice:
"Run Oracle DB Compatibility Checker 48h before — stored procedures are the silent POC killer."
"Confirm ADB-D shape availability in the target region before committing to the demo date."
```

---

CHANGE 2: agent/archie_wiring.py — append to _TOOL_SEQUENCING_RULES

Find _TOOL_SEQUENCING_RULES and add this section at the end, before the closing triple-quote:

```
### POC Planning Workflow

When the SE needs to know what to build for a customer:

1. Call generate_poc_plan (default: action="explore"). Runs 3 parallel evaluations.
   Returns ranked options with relevance score, build time, wow moment, pre-demo checklist.

2. Present options clearly. For each: name, relevance score (X/10), build time (Xh),
   wow moment, top 2 risks. Give your recommendation with rationale citing ≥2 specific
   customer facts (pain, platform, timeline, budget, industry, competitive context).
   End with: "Which option would you like to proceed with?"

3. Wait for confirmation. When the user selects — by number ("option 1"), by name
   ("the DB migration"), by description ("the cost one"), or by affirmation ("that one",
   "go", "yes", "let's do it") — extract the confirmed_option_name from the poc_options
   list and call:
     generate_poc_plan(action="confirm", confirmed_option_name="[exact option_name from list]")

4. The confirm call fans out all 5 artifacts simultaneously. When all complete, present
   as a package: "POC kit for [option_name] is ready: architecture diagram, BOM (~$X/mo),
   JEP execution plan, Terraform scripts, and client deck. [Download links.]"

5. Do NOT generate artifacts before the user confirms an option.
6. Do NOT call generate_poc_plan(action="explore") again after the user has confirmed.
7. If user changes their mind ("try option 2 instead", "actually use the AI angle"),
   call generate_poc_plan(action="confirm", confirmed_option_name="[option 2 name]").
8. If ambiguous, ask once: "Which option — the [name1] (Xh, Y/10) or the [name2]?"
```

---

Run ALL acceptance criteria:

  python3.11 -m py_compile agent/archie_wiring.py

  python3.11 -c "
  from pathlib import Path
  src = Path('agent/archie_wiring.py').read_text()
  checks = [
      ('POC Planning Workflow',             'POC workflow section header'),
      ('action=\"confirm\"',                'confirm mode reference'),
      ('confirmed_option_name',             'confirmed_option_name arg'),
      ('Do NOT generate artifacts before',  'no-artifacts-before-confirm rule'),
      ('stored procedures are the silent',  'proactive recommendation'),
      ('Compatibility Checker',             'specific proactive tip'),
      ('wrong audience',                    'risk instinct: audience mismatch'),
  ]
  for content, label in checks:
      assert content in src, f'FAIL: {label!r} missing — looking for: {content!r}'
  print('PASS: POC workflow sequencing and identity improvements present')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Only modify agent/archie_wiring.py (_EXPERT_IDENTITY and _TOOL_SEQUENCING_RULES sections).
No other files.
```

---

## Task p55e — Presentation: hat + synthesis handler + sub-agent + renderer

```
Context: No PPTX capability exists today. The deck must synthesize actual artifact
content — BOM line items, POV narrative, research differentiators — not just reference
artifact keys. The hat performs the synthesis; the sub-agent renders the spec.

Reference: https://github.com/aruanurag/oci-architecture-codex-skill
Uses oracle-oci-architecture-toolkit-v24.1.pptx as a master stencil.
Same pattern as OCI_Library.xml for draw.io icons.

IMPORTANT: Branch from origin/main. Independent of p55a/b/c/d.

  git fetch origin
  git checkout -b claude/p55e origin/main

---

CHANGE 1: requirements.txt — add:
  python-pptx>=1.0.2

---

FILE 2: agent/hats/oci_presentation_writer.md

New file. Format follows oci_bom_expert.md. Key content:

YAML frontmatter:
```yaml
---
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
    - bom_artifact_key
    - pov_artifact_key
    - research_artifact_key
    - diagram_artifact_key
  summary_style: "synthesis_oriented"
  include_full_memory: false
coordination:
  parallel_with:
    - "generate_diagram"
    - "generate_bom"
    - "generate_jep"
    - "generate_terraform"
  suggested_next_hat: null
---
```

Pre-Action Checklist must include:
1. poc_recommendation absent → NEEDS_CLARIFICATION: "No POC confirmed yet. Use generate_poc_plan first."
2. customer_name absent → NEEDS_CLARIFICATION: "What is the customer's name?"
3. Load BOM artifact (bom_artifact_key from memory) → extract: monthly_total, top 5 line items
4. Load POV artifact (pov_artifact_key) → extract: executive_summary first 2 paragraphs
5. Load research artifact (research_artifact_key) → extract: recommendation_rationale, top risks, competitive_differentiators
6. Build [PRESENTATION BRIEF] block with all loaded content (see Presentation spec in this plan)

Post-Action Review: 7 slides present, no placeholder text, customer name on title,
artifact_key ends in .pptx, BOM numbers match if BOM was loaded,
presenter_notes on every slide with ≥1 objection+response.

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
You receive a complete [PRESENTATION BRIEF] with actual customer content.
Use it verbatim. Do not substitute placeholders for provided content.

## Slide Structure (always exactly 7)

1. title       — POC name, customer name, date
2. challenge   — from SLIDE 2 in brief (customer pain, current state, in their words)
3. architecture — from SLIDE 3 in brief (services, topology description)
4. services    — key OCI services, one-liner each (what it does FOR THIS CUSTOMER)
5. cost        — from SLIDE 5 in brief (exact BOM line items, monthly_total)
6. why_oci     — from SLIDE 6 in brief (differentiators for this customer's concern)
7. next_steps  — from SLIDE 7 in brief (wow moment, success criteria, CTA)

## Presenter Notes Quality Standard

Every slide must have presenter_notes with:
- What to say on this slide (1-2 sentences)
- One expected objection and a specific response

Example for architecture slide:
"Walk left to right: on-prem DRG → VCN → ADB-D subnet. When asked 'how long does
provisioning take?', say '20 minutes — we pre-provisioned this one.' Objection:
'we're Oracle-licensed' → 'ADB-D preserves your license investment.'"

## Output Format (no markdown, no prose):

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
"""Renders a 7-slide OCI POC deck from JSON spec using python-pptx.
Uses oracle-oci-architecture-toolkit-v24.1.pptx for OCI icon stencils.
Falls back to text labels gracefully if toolkit is not present."""
from __future__ import annotations
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

    for slide_spec in sorted(spec.get("slides", []), key=lambda s: s.get("slide_number", 0)):
        slide  = prs.slides.add_slide(blank)
        layout = slide_spec.get("layout", "bullets")
        _set_background(slide, layout)
        _add_title(slide, slide_spec.get("title", ""), layout)
        _render_body(slide, slide_spec, layout)
        notes = slide_spec.get("presenter_notes", "")
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
        if spec.get("subtitle"):
            _textbox(slide, spec["subtitle"], Inches(1.5), Inches(4.0), Inches(10), Inches(1.2),
                     size=22, color=ORACLE_WHITE)
    elif layout == "architecture":
        _render_architecture(slide, spec.get("oci_services", []), spec.get("topology_description", ""))
    elif layout == "table":
        _render_table(slide, spec.get("rows", []), spec.get("total", ""))
    elif layout == "next_steps":
        _render_bullets(slide, spec.get("bullets", []))
        if spec.get("cta"):
            _textbox(slide, spec["cta"], Inches(0.5), Inches(6.4), Inches(12.3), Inches(0.7),
                     size=16, color=ORACLE_RED, bold=True)
    else:
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
        p.font.size = Pt(18)
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
    _textbox(slide, "Service                              Qty           Monthly Cost",
             Inches(0.5), y, Inches(12.3), Inches(0.4), size=13, bold=True)
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
                    target_slide.shapes._spTree.append(deepcopy(shape._element))
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
    p.font.size = Pt(size)
    p.font.bold = bold
    p.font.color.rgb = color
```

---

FILE 8: sub_agents/presentation/server.py

Copy sub_agents/pov/server.py. Adapt:
- agent_name = "presentation"
- AgentCard: inputs=["task", "customer_name", "poc_name"], required=["task", "customer_name"]
- Port 8090
- After LLM returns JSON spec: call render_oci_powerpoint.render(spec, tmp_path),
  read bytes, return base64-encoded in A2AResponse.result

```python
import base64, json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from sub_agents.presentation.scripts import render_oci_powerpoint

# Inside handle():
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
"""Forge tool handler for generate_presentation.
Synthesizes actual artifact content before calling the sub-agent."""
import base64
import logging

from agent import sub_agent_client
from skillforge.types import ToolResult

logger = logging.getLogger(__name__)


class PresentationHandler:
    def __init__(self, store, customer_id: str, customer_name: str):
        self._store         = store
        self._customer_id   = customer_id
        self._customer_name = customer_name

    async def __call__(self, args, *, memory, context, trace_id) -> ToolResult:
        dc         = getattr(memory, "decision_context", {}) or {}
        poc_option = args.get("poc_option") or dc.get("poc_recommendation") or {}
        poc_name   = poc_option.get("option_name") or poc_option.get("poc_name") or "OCI POC"

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
            engagement_context={"poc_name": poc_name, "customer_name": self._customer_name},
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

Register:

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

CHANGE 11: drawing_agent_server.py — in /download endpoint, add before default return:

```python
if artifact_key.endswith(".pptx"):
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{artifact_key.split("/")[-1]}"'},
    )
```

CHANGE 12: config.yaml — add to sub_agents section:
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
  import sys, inspect; sys.path.insert(0, '.')
  from agent.tools.presentation import PresentationHandler
  src = inspect.getsource(PresentationHandler.__call__)
  assert '_load_artifact' in src, 'FAIL: artifact synthesis missing'
  assert 'bom_content'    in src, 'FAIL: BOM synthesis missing'
  assert 'pov_content'    in src, 'FAIL: POV synthesis missing'
  print('PASS: PresentationHandler synthesizes actual artifact content')
  "

  python3.11 -c "
  import sys, zipfile, tempfile, os; sys.path.insert(0, '.')
  from sub_agents.presentation.scripts.render_oci_powerpoint import render
  spec = {'slides': [
    {'slide_number':1,'layout':'title','title':'OCI POC for Acme','subtitle':'Solutions Review','presenter_notes':'Open by confirming agenda.'},
    {'slide_number':2,'layout':'bullets','title':'Acme faces \$2M infrastructure cost','bullets':['Oracle RAC on-prem, \$2.1M/yr','EOL in 18 months'],'presenter_notes':'Confirm resonates. Objection: this is our estimate → confirm with their team.'},
    {'slide_number':3,'layout':'architecture','title':'ADB-Dedicated in us-chicago-1','oci_services':[{'name':'Autonomous Database','icon':'OCI_Autonomous_Database'}],'topology_description':'Single AD, dedicated subnet','presenter_notes':'Walk left to right. Objection: how long to provision? → 20 min, pre-provisioned.'},
    {'slide_number':4,'layout':'services','title':'Key OCI Services','services':[{'name':'ADB-D','description':'Managed Exadata — no patching, autonomous tuning'}],'presenter_notes':''},
    {'slide_number':5,'layout':'table','title':'Cost: \$644/mo vs \$175K/yr','rows':[{'service':'ADB (2 ECPU)','qty':'1','monthly_cost':'\$400'}],'total':'\$644/mo','presenter_notes':'Use exact numbers. Objection: is this the full cost? → yes, includes storage.'},
    {'slide_number':6,'layout':'bullets','title':'Why OCI: Real Exadata','bullets':['ADB-D runs on Exadata — not shared emulation'],'presenter_notes':''},
    {'slide_number':7,'layout':'next_steps','title':'Next: Migrate test DB in 4 hours','bullets':['Pre-provision ADB-D'],'cta':'Run Acme AR query — show time delta live','presenter_notes':'Confirm success criteria before leaving.'},
  ]}
  with tempfile.NamedTemporaryFile(suffix='.pptx', delete=False) as f:
      tmp = f.name
  render(spec, tmp)
  assert os.path.getsize(tmp) > 1000
  assert zipfile.is_zipfile(tmp)
  from pptx import Presentation as P
  prs = P(tmp)
  assert len(prs.slides) == 7, f'Expected 7 slides, got {len(prs.slides)}'
  os.unlink(tmp)
  print('PASS: render produces valid 7-slide PPTX')
  "

  pytest tests/ -q --tb=short -m "not live" 2>&1 | tail -10

One PR. Do not modify any other files.
```

---

## Run Order

```
p55a (background jobs + Telegram)   ─┐
p55b (POC Strategist hat)            ├── independent, run in parallel
p55e (Presentation)                  ─┘
p55c (POC Strategist tool)          ── after p55b
p55d (system prompt)                ── after p55b + p55c
```

## Critical Files

| File | Task | Change |
|---|---|---|
| `skillforge/forge.py` | p55a | Add `run_turn_background()` |
| `drawing_agent_server.py` | p55a, p55e | Background endpoint + PPTX content-type |
| `agent/notifications.py` | p55a | Implement Telegram (replace TODO stub) |
| `config.yaml` | p55a, p55c, p55e | Telegram + 3 new sub-agent URLs |
| `ui/src/components/ChatInterface.tsx` | p55a | Background mode toggle + poll |
| `agent/hats/oci_poc_strategist.md` | p55b | New hat — expert POC thinking lens |
| `sub_agents/poc_strategist/` | p55c | New sub-agent (4 files) |
| `agent/tools/specialists.py` | p55c | `PocStrategistHandler` (explore + confirm modes) |
| `agent/archie_wiring.py` | p55c, p55d, p55e | 2 new tools + `_EXPERT_IDENTITY` + POC sequencing |
| `agent/hats/oci_presentation_writer.md` | p55e | New hat — synthesis pre-action |
| `sub_agents/presentation/` | p55e | New sub-agent (6 files + assets/) |
| `agent/tools/presentation.py` | p55e | `PresentationHandler` (artifact synthesis) |
| `requirements.txt` | p55e | Add `python-pptx>=1.0.2` |

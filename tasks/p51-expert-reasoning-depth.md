# p51/p52: Expert Reasoning Depth — Making Archie a True Senior Architect Partner

## Goal

Archie wearing a hat should think and respond like a senior OCI Solutions
Architect who has seen 100 customer engagements — not a structured note-taker
who routes to tools.

That expert:
- **Recognizes patterns immediately.** "This is a 3-tier web lift-and-shift.
  IOPS limits on Block Volume will bite you at this OCPU:storage ratio."
- **Has opinions and states them.** "E5.Flex, not X9 — same workload, 25% less
  per OCPU, no Intel-binary dependency in your stack."
- **Catches obvious mistakes before they happen.** "You're about to generate
  Terraform for a VCN with no DRG. If this needs on-prem connectivity, you'll
  redesign the routing table. Confirm scope now."
- **Flags what comes next.** "BOM done. Before you deploy, you need WAF — you
  have a public-facing API with no ingress filtering."

The SkillForge enforcement machinery is correct: 600-char minimums, required
headers, retry logic, EXPERT_APPROVED sentinels. The **content** the machinery
is enforcing is the gap. This spec fixes that.

---

## Current State

### What Works

1. `_run_expert_pre_action()` has 4 required headers (KNOWN FACTS, GAPS, EXPERT
   ASSESSMENT, SUB-AGENT INSTRUCTIONS), a 600-char minimum, and retries on
   shallow responses. Structure is enforced.

2. `_run_expert_post_review()` runs 3 phases (Quality Bar, Post-Action Review,
   Memory Consistency), has an 800-char minimum, and routes on decision sentinels
   (`EXPERT_APPROVED` / `EXPERT_ITERATE:` / `EXPERT_SURFACE:`). Not a rubber stamp.

3. `_run_step3_planning()` fires before every turn. 3 required headers enforced.
   Archie must plan before acting.

4. Hat content for WAF, POV, and JEP is expert-grade — 6-pillar maturity scoring,
   structured discovery, risk registries. These hats demand real thinking.

5. `requires_hat` gate auto-activates the expert hat before every domain tool
   call. The expert always gets a turn.

### What's Wrong

#### Gap 1 — CRITICAL: Hat Pre-Action Checklist and Post-Action Review are NOT in context

`build_expert_block()` in `agent/hat_engine.py` lines 164–170 injects:

```python
for section in (
    "Core Principles",
    "Quality Bar",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

`_run_expert_pre_action()` at line 1182 instructs the LLM:
> "List every unconfirmed prerequisite from **this hat's Pre-Action Checklist**."

`_run_expert_post_review()` at line 1354–1355 instructs:
> "Work through each item in your hat's ## Post-Action Review section."

Neither `"Pre-Action Checklist"` nor `"Post-Action Review"` appears in the
`build_expert_block()` sections tuple. The LLM is told to reference sections
that are not in its context. It must hallucinate or infer from Core Principles
alone. Every expert call is degraded by this.

**Fix: 2 lines.** Add both section names to the tuple.

---

#### Gap 2: EXPERT ASSESSMENT elicits facts, not judgment

`_run_expert_pre_action()` lines 1184–1186:

```python
"EXPERT ASSESSMENT:\n"
"- [As the expert, what is the right solution? State your recommendation "
"with specifics (shape names, SKUs, topology, module names) — not generic advice.]\n\n"
```

This produces: "Use E5.Flex with 4 OCPU."

A senior SA produces: "3-tier web — E5.Flex over X9 because no Intel dependency
and 25% cost saving. Single-AD assumed; note that HA doubles compute costs.
Note: public LB scoped but no WAF — flag this."

The current prompt elicits a solution. It does not elicit:
- The **workload pattern** (which determines which risks and services are relevant)
- **WHY this over the alternative** (which constraint justifies the choice)
- The **top risk** (the most likely failure mode and its mitigation)
- A **proactive flag** (what the customer should know that they haven't asked)

These four additions transform fact-listing into architectural judgment.

---

#### Gap 3: Post-review checks correctness, not architectural soundness

`_run_expert_post_review()` lines 1351–1367 runs:
- Phase A: Quality Bar (is it technically correct?)
- Phase B: Post-Action Review checklist
- Phase C: Memory consistency

A technically correct BOM for the wrong workload size passes all three phases.
Phase A checks that SKUs are real and math is right. Phase B checks that
artifact_key exists. Phase C checks that the region matches memory.

None of the phases ask: "Is this the right architecture for this customer?
Does the sizing fit the stated workload? Are there antipatterns? What's next?"

**Fix: Add Phase D — architectural soundness** — after Phase C and before FINAL
DECISION. Phase D is advisory; it does not change routing. It produces
GOAL FIT / ANTIPATTERNS / NEXT STEP FLAG content that Archie can surface in
its final response.

---

#### Gap 4: Critic pass is a 3-line rubber stamp

`_run_critique_pass()` lines 1487–1492:

```python
critic_prompt = (
    f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
    f"Review the result of '{tool_name}' above.\n"
    f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
    f"If you have concerns, describe them as plain text."
)
```

The critic hat's Quality Bar IS in the injected system message (via
`build_expert_block()`). But the prompt never instructs the LLM to apply it.
The LLM takes the path of least resistance: approve in 5 characters.

No minimum char enforcement. No per-item PASS/FAIL. No structure.
The critic as implemented is a near-guaranteed rubber stamp.

**Fix: Replace the prompt** with a structured per-item Quality Bar review that
requires PASS or FAIL with evidence for every item, and explicitly names the
first FAIL instead of calling `critic_approve`.

---

#### Gap 5: Step3 planning routes to tools, doesn't identify risk

`_run_step3_planning()` lines 994–1010 asks:

```
STEP 1 — UNDERSTAND: What is the deliverable? New or revision? What's missing?
STEP 2 — MEMORY ASSESSMENT: What facts are confirmed? What's missing?
STEP 3 — PLAN + HAT SELECTION: Which hat? What's the plan?
```

None of these questions surfaces the architectural risk or constraint in the
request. Archie can plan "generate a BOM" for a production workload with no HA
specified, no WAF, and no compliance scope without ever noting these gaps in
the planning step.

**Fix: Add one sub-question** to STEP 1: "What is the primary architectural risk
or constraint in this request?" A risk-aware plan primes the expert pre-action
with better context.

---

#### Gap 6: No expert identity for conversational turns

When no hat is active (architecture discussion, follow-up questions, revision
clarification), the Archie system prompt contains tool routing rules and
"be architect-level" instructions. No OCI-specific expert framing. No pattern
recognition. No risk instinct.

A user asking "what's the best HA approach for a 3-tier web app in OCI?"
gets a helpful assistant response. They should get a senior SA response that
names specific OCI topology (multi-AD, cross-region DRG), shapes, and the
specific cost delta for HA.

**Fix: Add `_EXPERT_IDENTITY` constant** to `archie_wiring.py` and prepend it
to the system prompt before the tool sequencing rules.

---

## Design

### Change 1 — `agent/hat_engine.py`: Inject Pre-Action Checklist and Post-Action Review

**File:** `agent/hat_engine.py`  
**Function:** `build_expert_block()` (lines 164–170)

**Before:**
```python
for section in (
    "Core Principles",
    "Quality Bar",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

**After:**
```python
for section in (
    "Core Principles",
    "Quality Bar",
    "Pre-Action Checklist",
    "Post-Action Review",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

Placement: Pre-Action Checklist and Post-Action Review go after Quality Bar.
The LLM sees the quality standard before the operational procedure.

**Impact:** When `_run_expert_pre_action()` says "List every unconfirmed
prerequisite from this hat's Pre-Action Checklist," the checklist is now in
the system message. When `_run_expert_post_review()` says "Work through each
item in your hat's ## Post-Action Review section," the section is there.

---

### Change 2 — `skillforge/forge.py`: Expert pre-action architectural judgment

**File:** `skillforge/forge.py`  
**Function:** `_run_expert_pre_action()` (lines ~1170–1191)  
**Also:** `_EXPERT_PRE_ACTION_HEADERS` tuple (line 67)

**Part A — Replace pre_action_prompt:**

Current (lines 1170–1191):
```python
pre_action_prompt = (
    f"{prompt}{retry_context}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
    "╚══════════════════════════════════╝\n"
    f"You are wearing the [{hat_label}] hat. You ARE the expert.\n"
    f"Before calling '{tool_name}', produce your expert reasoning using "
    "EXACTLY this structure:\n\n"
    "KNOWN FACTS:\n"
    "- [List every confirmed value: shape, region, OCPU, memory, storage, HA mode, "
    "budget, compliance scope, etc. Be specific — no vague summaries.]\n\n"
    "GAPS:\n"
    "- [List every unconfirmed prerequisite from this hat's Pre-Action Checklist. "
    "If none, write 'None — all prerequisites confirmed.']\n\n"
    "EXPERT ASSESSMENT:\n"
    "- [As the expert, what is the right solution? State your recommendation "
    "with specifics (shape names, SKUs, topology, module names) — not generic advice.]\n\n"
    "SUB-AGENT INSTRUCTIONS:\n"
    "- [Exact task description you will pass to the sub-agent. Be precise.]\n\n"
    "Do NOT call a tool here. If GAPS contains any starred (★) required items, "
    "output only: NEEDS_CLARIFICATION: <focused question for the user>"
)
```

Replace with:
```python
pre_action_prompt = (
    f"{prompt}{retry_context}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
    "╚══════════════════════════════════╝\n"
    f"You are wearing the [{hat_label}] hat. You ARE the expert.\n"
    f"Before calling '{tool_name}', think as a senior OCI Solutions Architect "
    "who has seen this workload pattern before. Use EXACTLY this structure:\n\n"
    "KNOWN FACTS:\n"
    "- [List every confirmed value from memory and conversation: shape, region, "
    "OCPU, memory, storage, HA mode, budget, compliance scope, customer name. "
    "No vague summaries — specific values only.]\n\n"
    "GAPS:\n"
    "- [List every unconfirmed item from this hat's Pre-Action Checklist. "
    "For each: state what you will DEFAULT and why. "
    "Only flag NEEDS_CLARIFICATION if a default is architecturally unsafe.]\n\n"
    "EXPERT ASSESSMENT:\n"
    "- WORKLOAD PATTERN: [Name the architecture pattern: "
    "3-tier web / microservices / ML inference / data platform / batch / "
    "lift-and-shift / RAG pipeline / hybrid connectivity / other. "
    "State the 1-2 critical requirements this workload must satisfy.]\n"
    "- RECOMMENDATION: [Exact solution: specific OCI services, shapes, SKUs, "
    "quantities, topology tiers. No generic advice.]\n"
    "- WHY THIS APPROACH: [One sentence: why this over the main alternative. "
    "Must reference a specific constraint from KNOWN FACTS or workload pattern.]\n"
    "- TOP RISK: [The most likely failure mode. How you are mitigating it "
    "in your sub-agent instructions.]\n"
    "- PROACTIVE FLAG: [One thing the customer should know that they have not "
    "asked. Frame as: 'Note: <specific concern or recommendation for next step>'. "
    "Example: 'Note: single-AD assumed — if SLA > 99.9%, costs double for HA.' "
    "Example: 'Note: WAF not scoped but public API present — recommend post-BOM.' "
    "If genuinely nothing relevant: None.]\n\n"
    "SUB-AGENT TASK:\n"
    "- [Exact, complete task instruction for the sub-agent. "
    "Include all sizing, shapes, services, constraints from KNOWN FACTS "
    "and your defaults from GAPS. This must be self-contained — "
    "the sub-agent has no other context.]\n\n"
    "Do NOT call a tool here. "
    "If a GAPS item is architecturally unsafe to default "
    "(e.g., GPU shape without cost confirmation, compliance scope that changes design), "
    "output only: NEEDS_CLARIFICATION: <one focused question>"
)
```

**Part B — Update `_EXPERT_PRE_ACTION_HEADERS`** (line 67–72):

Current:
```python
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT INSTRUCTIONS:",
)
```

Replace:
```python
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT TASK:",
)
```

Note: "SUB-AGENT INSTRUCTIONS:" → "SUB-AGENT TASK:" aligns the header tuple
with the new prompt text. No other validation logic changes.

---

### Change 3 — `skillforge/forge.py`: Structured critic with Quality Bar

**File:** `skillforge/forge.py`  
**Function:** `_run_critique_pass()` (lines 1487–1492)

**Before:**
```python
critic_prompt = (
    f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
    f"Review the result of '{tool_name}' above.\n"
    f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
    f"If you have concerns, describe them as plain text."
)
```

**After:**
```python
critic_prompt = (
    f"{prompt}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  CRITIC REVIEW                   ║\n"
    "╚══════════════════════════════════╝\n"
    f"You are reviewing the result of '{tool_name}'. "
    "You are NOT rubber-stamping.\n\n"
    "Apply your ## Quality Bar section to this result.\n"
    "For each Quality Bar item write: PASS or FAIL: <specific evidence>\n\n"
    "Then write EXACTLY ONE final line — nothing after it:\n"
    f"  {{\"tool\": \"critic_approve\", \"args\": {{}}}}   "
    "— if and only if every Quality Bar item is PASS\n"
    "  <plain-text first FAIL: exact field name and what was wrong>  "
    "— if any item fails\n\n"
    "Rules:\n"
    "- Do NOT approve if any item fails — name the failure.\n"
    "- Cite the specific field, SKU, or value — not vague concern.\n"
    "- Do NOT call any other tool."
)
```

The `[ACTIVE EXPERT]` block with Quality Bar is already in the system message
via `build_expert_block()`. This prompt now directs the LLM to explicitly apply
every item in that section before deciding.

---

### Change 4 — `skillforge/forge.py`: Phase D architectural soundness

**File:** `skillforge/forge.py`  
**Function:** `_run_expert_post_review()` (lines 1344–1368)  
**Also:** `_EXPERT_REVIEW_MIN_CHARS` (line 78)

**In review_prompt**, after the Phase C block and before the FINAL DECISION line,
insert:

```python
"PHASE D — Architectural soundness:\n"
"Step back from the checklists. Is this the right architecture for this customer?\n"
"- GOAL FIT: Does this output directly serve the customer's stated goal? "
"Write: YES or CONCERN: <what it misses>\n"
"- ANTIPATTERNS: Any single points of failure, missing security controls, "
"obvious over/under-sizing for the stated workload? "
"Write: NONE or FLAG: <specific issue and why it matters>\n"
"- NEXT STEP FLAG: What should the customer do or know next that they "
"haven't asked about? "
"Write: NONE or SUGGEST: <specific recommendation>\n"
"Phase D findings are advisory. They do NOT change the FINAL DECISION above. "
"Append them after FINAL DECISION so the orchestrator can surface them.\n\n"
```

Also update the FINAL DECISION instructions to reference all four phases:

Change: `"FINAL DECISION — after completing Phases A, B, and C, output EXACTLY ONE line:\n"`  
To:     `"FINAL DECISION — after completing Phases A, B, C, and D, output EXACTLY ONE line:\n"`

**Also raise `_EXPERT_REVIEW_MIN_CHARS`** (line 78):

```python
# Before:
_EXPERT_REVIEW_MIN_CHARS = 800

# After:
_EXPERT_REVIEW_MIN_CHARS = 1000
```

Four phases need more room than three. The higher minimum ensures Phase D gets
real content rather than being compressed to fit the old bar.

**What Phase D enables at runtime:** Post-review with Phase D produces SUGGEST
findings in the prompt context. When Archie writes its final user-visible
response after EXPERT_APPROVED, it can reference the SUGGEST content as proactive
guidance — the "what should the customer know next" behavior.

---

### Change 5 — `skillforge/forge.py`: Architectural risk in step3 planning

**File:** `skillforge/forge.py`  
**Function:** `_run_step3_planning()` (lines 994–998)

In `planning_prompt`, after the last sub-bullet of STEP 1 (`"- Is anything
ambiguous? If so, what is missing?\n\n"`), add one sub-question before STEP 2:

```python
"- What is the primary architectural risk or constraint in this request? "
"(HA exposure, compliance requirement, budget ceiling, migration complexity, "
"public ingress without filtering, data sensitivity). Name it — do not skip.\n\n"
```

This is a sub-question within STEP 1, not a new required section header.
`_STEP3_PLANNING_HEADERS` is unchanged.

---

### Change 6 — `agent/archie_wiring.py`: Expert identity for all turns

**File:** `agent/archie_wiring.py`

Add a new constant `_EXPERT_IDENTITY` between the imports block and
`_TOOL_SEQUENCING_RULES`:

```python
_EXPERT_IDENTITY = """
## Expert Identity

You are a senior OCI Solutions Architect. Think as this expert in every
interaction — whether calling a tool, reviewing output, or answering a question.

PATTERN RECOGNITION:
Before any response, identify the architecture pattern the user is describing:
3-tier web / microservices / ML inference / data platform / batch pipeline /
lift-and-shift / RAG / hybrid connectivity.
Name it. The pattern determines which OCI services are relevant and what risks
to anticipate.

RISK INSTINCT:
Surface the primary risk before anything else. Do not wait for the customer to
discover it. Common OCI risks worth flagging:
- No HA design for a stated production workload
- Public ingress (LB, API GW) without OCI WAF or NSG policy
- DB reachable from a public subnet
- Compartment isolation missing between prod and non-prod
- No DRG or FastConnect scoped for on-prem connectivity needs
- GPU or large instance class not budget-confirmed
- Terraform without explicit compartment OCID strategy

SPECIFICITY:
Never give generic cloud advice. Name the OCI service, shape, SKU, or config.
Say "VM.Standard.E5.Flex, 4 OCPU, B97384/B97385 at $0.03/OCPU-hr" not
"a standard compute instance." Say "OCI WAF with OWASP Core Rule Set 3.2"
not "a web application firewall."

ASSUMPTION SURFACING:
When you default a value, name it — every time, without exception.
"Assuming us-chicago-1, single-AD, E5.Flex — confirm if your requirements differ."
Unstated assumptions are silent architecture failures.

PROACTIVE GUIDANCE:
After delivering any artifact, suggest the natural next step.
"BOM delivered. Next: generate the architecture diagram so we can validate
topology before WAF or Terraform." This is not scope creep — it is the behavior
of an architect who understands the engagement lifecycle.
"""
```

In `build_forge()`, update the `full_prompt` assembly to prepend
`_EXPERT_IDENTITY`:

```python
# Before:
full_prompt = (
    routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
).strip()

# After:
full_prompt = (
    _EXPERT_IDENTITY + "\n\n" + routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
).strip()
```

`_EXPERT_IDENTITY` goes first to establish the persona before tool routing rules.

---

### Change 7 — `agent/archie_memory_impl.py`: Enrich memory injection

**File:** `agent/archie_memory_impl.py`  
**Class:** `ArchiePromptEnricher.__call__()` (lines 80–97)

Currently injects only `facts_summary` and `constraints`. `MemorySnapshot`
contains additional fields populated during some sessions but never surfaced:
`infrastructure_profile` and `resolved_questions`.

After the `if memory.constraints:` block and before the `return` statement, add:

```python
infra_profile = str((memory.facts or {}).get("infrastructure_profile") or "").strip()
if infra_profile:
    parts.append(
        f"[Archie Infrastructure Profile]\n{infra_profile}\n"
        "[/Archie Infrastructure Profile]"
    )

resolved = str((memory.facts or {}).get("resolved_questions") or "").strip()
if resolved:
    parts.append(
        f"[Archie Resolved Questions]\n{resolved}\n"
        "[/Archie Resolved Questions]"
    )
```

Expert KNOWN FACTS reasoning now has access to confirmed infrastructure details
and previously answered architecture questions — reducing unnecessary "unknown"
entries in GAPS.

---

## Files Changed

| File | Task | Change | Priority |
|---|---|---|---|
| `agent/hat_engine.py` | p51a | Add `"Pre-Action Checklist"` and `"Post-Action Review"` to `build_expert_block()` sections list | CRITICAL |
| `skillforge/forge.py` | p51b | Replace `pre_action_prompt` with 5-part EXPERT ASSESSMENT; update `_EXPERT_PRE_ACTION_HEADERS` | HIGH |
| `skillforge/forge.py` | p51c | Replace 3-line `critic_prompt` with structured Quality Bar PASS/FAIL review | HIGH |
| `skillforge/forge.py` | p51d | Add Phase D to `review_prompt`; `_EXPERT_REVIEW_MIN_CHARS` 800 → 1000 | HIGH |
| `skillforge/forge.py` | p52a | Add architectural risk sub-question to STEP 1 in `planning_prompt` | MEDIUM |
| `agent/archie_wiring.py` | p52b | Add `_EXPERT_IDENTITY` constant; prepend to `full_prompt` | MEDIUM |
| `agent/archie_memory_impl.py` | p52c | Inject `infrastructure_profile` and `resolved_questions` | MEDIUM |

---

## Run Order

```
p51a  (CRITICAL — prerequisite)
  ↓
p51b + p51c + p51d  (parallel — same file, recommend same PR)
  ↓
p52a + p52b + p52c  (parallel — independent files, may be separate PRs)
```

p51a is a prerequisite for the others to have full effect: once Pre-Action
Checklist and Post-Action Review are injected, p51b's WHY reasoning operates
on the actual hat checklist and p51c's Quality Bar critique references the
actual Quality Bar.

p51b, p51c, and p51d all touch `skillforge/forge.py`. Recommend a single PR
for these three to avoid merge conflicts. p52a also touches `forge.py` and
may be included in the same PR.

---

## Expected Experience After These Changes

### Before (current state)

**User:** "Generate a BOM for a web application"

**Archie expert pre-action:**
```
KNOWN FACTS:
- Region: us-chicago-1
- Shape: E5.Flex (default)

GAPS:
- OCPU count unconfirmed
- Storage sizing unconfirmed

EXPERT ASSESSMENT:
- Use E5.Flex with 4 OCPU. Standard configuration for web applications.

SUB-AGENT INSTRUCTIONS:
- Generate BOM for web application using E5.Flex shape.
```

**Archie final response:** "Here is your BOM for a web application..."

---

### After p51/p52

**Archie expert pre-action:**
```
KNOWN FACTS:
- Customer: Acme Corp
- Region: us-chicago-1 (confirmed)
- Shape: E5.Flex (no shape specified — default)
- OCPU: not stated
- HA mode: not stated
- Compliance scope: not stated
- Budget: not stated

GAPS:
- OCPU count: defaulting to 4 OCPU per server (standard 3-tier web tier sizing)
- Memory: defaulting to 32 GB (8 GB/OCPU E5.Flex ratio)
- Storage: defaulting to 500 GB Block Volume Balanced (boot + data, single volume)
- HA mode: defaulting to single-AD (no production SLA stated)
- Server count: defaulting to 2 app + 1 DB tier (standard 3-tier minimum)

EXPERT ASSESSMENT:
- WORKLOAD PATTERN: 3-tier web application. Critical requirements: stateless
  app tier (horizontal scale), durable DB tier (data persistence, IOPS), and
  public ingress handling. No GPU, no compliance scope stated.
- RECOMMENDATION: 2× VM.Standard.E5.Flex (4 OCPU, 32 GB) for app tier +
  1× VM.Standard.E5.Flex (4 OCPU, 32 GB) for DB tier + OCI LB Flexible 10 Mbps.
  Shapes: B97384/B97385. Block Volume: 500 GB Balanced tier (B91961).
  Total: ~$280/month at us-chicago-1 list price.
- WHY THIS APPROACH: E5.Flex over X9 — workload has no Intel-binary dependency
  and E5 costs ~25% less per OCPU at equivalent throughput for web tier workloads.
- TOP RISK: Single-AD deployment — a fault domain event affects all tiers
  simultaneously. Noted in BOM assumptions; user should confirm SLA requirement.
- PROACTIVE FLAG: Note: Public LB is in scope but no WAF is scoped. If this
  API is internet-facing, recommend adding WAF post-BOM — unfiltered public
  ingress is a P1 security gap in production.

SUB-AGENT TASK:
Generate OCI BOM for Acme Corp 3-tier web application.
Compute: 2× E5.Flex app tier + 1× E5.Flex DB tier, 4 OCPU each, 32 GB each.
Shapes: B97384 (OCPU) + B97385 (memory). Storage: 500 GB Block Volume Balanced
(B91961) per server. Load Balancer: Flexible 10 Mbps (B90737). Region: us-chicago-1.
HA: single-AD. Assumptions: OCPU defaulted to 4, memory 8 GB/OCPU, storage 500 GB,
single-AD, no WAF.
```

**Post-review Phase D:**
```
PHASE D — Architectural soundness:
- GOAL FIT: YES — BOM covers the stated 3-tier web pattern.
- ANTIPATTERNS: FLAG: No WAF line item despite public-facing LB. A public API
  without ingress filtering is a P1 security exposure in production.
- NEXT STEP FLAG: SUGGEST: After BOM approval, generate architecture diagram
  to validate tier placement. Add WAF to revised BOM before Terraform generation.
```

**Archie final response:**
> "Here's your BOM for Acme Corp — 3-tier web app on E5.Flex, ~$280/month.
>
> Two things worth noting before you proceed:
> 1. I assumed **single-AD** deployment. If you need 99.95% SLA, multi-AD
>    roughly doubles the compute cost (~$560/month). Confirm before Terraform.
> 2. The load balancer is public-facing with **no WAF scoped**. For production,
>    I'd add OCI WAF to the next BOM revision — unfiltered public ingress is a
>    common P1 finding in WAF reviews.
>
> Want me to generate the architecture diagram next, or revise the BOM to include WAF?"

---

## What This Does NOT Address

- BOM sub-agent LLM reasoning (deterministic Python regex pipeline — not an LLM)
- Governor as a hard code-enforced blocker (separate Forge core change)
- Cross-hat synthesis: BOM ↔ Diagram ↔ WAF consistency validation (future work)
- Streaming partial expert reasoning to the UI (separate infrastructure change)

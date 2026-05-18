# p51/p52 — Expert Reasoning Depth: Making Archie a Senior Architect Partner

## The Goal

Not "structured sections with good content." The goal is an Archie that thinks
like a senior OCI SA who has seen 100 customer engagements. That expert:

- **Recognizes patterns immediately.** "This is a LAMP stack lift-and-shift.
  They'll hit block volume IOPS limits if compute sizing is wrong."
- **Has opinions and states them.** "I'd use E5.Flex. E6 is same price but lower
  AD availability in us-chicago-1 — no benefit here."
- **Catches obvious mistakes before they happen.** "You're about to generate
  Terraform for a VCN without a DRG. If this needs on-prem connectivity later,
  you'll redesign the routing table. Confirm scope now."
- **Flags what comes next.** "BOM done. Before you deploy, you need WAF — you've
  got a public-facing API with no ingress filtering."

The current framework has the enforcement machinery right (600 char min, retries,
sentinels). The **content** of the expert reasoning is the gap. This spec fixes that.

---

## Architecture: How Expert Reasoning Flows Today

Understanding where reasoning happens is critical to knowing what to fix.

```
User message
     │
     ▼
forge.run_turn()
     │
     ├─ step3_planning          LLM call: STEP 1/2/3 structure
     │    Decides: deliverable, confirmed facts, which hat to activate
     │    Gap: never asks "what is the primary architectural risk?"
     │
     ├─ requires_hat gate       Auto-activates expert hat for domain tools
     │
     ├─ _run_expert_pre_action  LLM call with expert system message
     │    hat_engine injects: Core Principles, Quality Bar, Output Contract,
     │                        Critic Evaluation Guidance, Failure Questions
     │    Gap 1: Pre-Action Checklist and Post-Action Review NOT injected
     │    Gap 2: EXPERT ASSESSMENT elicits "what?" not "why?" or "what risk?"
     │
     ├─ tool dispatch            Calls registered handler (BomHandler, etc.)
     │    Sub-agent executes (deterministic pipeline for BOM)
     │
     ├─ _run_expert_post_review LLM call: 3-phase review
     │    Phase A: Quality Bar checks
     │    Phase B: Post-Action Review (hat checklist — but it's NOT injected)
     │    Phase C: Memory consistency
     │    Gap: No Phase D — "is this the right architecture for this customer?"
     │
     └─ _run_critique_pass      LLM call: critic evaluation
          Gap: 3-line unstructured prompt, never applies Quality Bar
```

---

## Root Cause Analysis

### Root Cause 1 — CRITICAL: Missing hat sections in expert context

`build_expert_block()` in `agent/hat_engine.py` (lines 165–174) builds the
`[ACTIVE EXPERT]` system message block that the LLM sees during expert calls.
It includes:

```python
for section in (
    "Core Principles",
    "Quality Bar",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

It does NOT include `"Pre-Action Checklist"` or `"Post-Action Review"`.

Yet `_run_expert_pre_action()` (forge.py ~line 1182) says:
> "List every unconfirmed prerequisite from **this hat's Pre-Action Checklist**."

And `_run_expert_post_review()` (forge.py ~line 1355) says:
> "Work through each item in **your hat's ## Post-Action Review section**."

The LLM is instructed to reference sections that are not in its context. It
must hallucinate or infer from Core Principles alone. Every hat has carefully
crafted checklists that are never seen by the expert.

**Fix:** Two lines — add `"Pre-Action Checklist"` and `"Post-Action Review"` to
the sections tuple. Maximum impact per line of code in this entire codebase.

---

### Root Cause 2: Expert pre-action is fact-listing, not architectural judgment

The `EXPERT ASSESSMENT` section in `_run_expert_pre_action()` asks:
> "As the expert, what is the right solution? State your recommendation with
> specifics (shape names, SKUs, topology, module names) — not generic advice."

This produces a recommendation but elicits no architectural thinking:
- No workload pattern identification
- No justification of *why this approach over alternatives*
- No identification of the primary risk
- No proactive customer guidance ("you haven't asked but you should know...")

The difference between a junior consultant and a senior SA is not whether they
know the right answer — it's whether they can explain why, identify what could
go wrong, and anticipate what comes next.

**Fix:** Replace the single EXPERT ASSESSMENT bullet with five structured
sub-bullets: WORKLOAD PATTERN, RECOMMENDATION, WHY THIS APPROACH, TOP RISK,
PROACTIVE FLAG.

---

### Root Cause 3: Post-review checks correctness but not architectural soundness

The three post-review phases check:
- Phase A: Does this meet the technical Quality Bar?
- Phase B: Does this pass the Post-Action Review checklist?
- Phase C: Is this consistent with what's in memory?

None of them ask:
- Is this the right architecture for this customer's actual goal?
- Are there single points of failure or obvious security gaps?
- What should the customer do next that they haven't asked about?

A BOM that is technically correct but sized for a test workload when the customer
said "production" passes all three phases. Phase D — architectural soundness —
is the senior SA lens that catches fit-for-purpose failures and surfaces
proactive guidance.

Phase D findings are **advisory only** — they do not change the EXPERT_APPROVED
/ EXPERT_ITERATE / EXPERT_SURFACE routing decision. They produce structured flags
that Archie can reference when writing its final response to the user.

---

### Root Cause 4: Critic pass is a 3-line rubber stamp

`_run_critique_pass()` (forge.py ~lines 1487–1492):

```python
critic_prompt = (
    f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
    f"Review the result of '{tool_name}' above.\n"
    f"If the result is acceptable, call: {\"tool\": \"critic_approve\", \"args\": {}}\n"
    f"If you have concerns, describe them as plain text."
)
```

No structure. No minimum chars. No instruction to apply the Quality Bar that IS
in the injected `[ACTIVE EXPERT]` system message. An LLM receiving this will
take the path of least resistance and approve immediately.

**Fix:** Replace with structured prompt that explicitly directs the LLM to apply
the hat's `## Quality Bar` section, write PASS or FAIL for each item, and forbid
approval if any item fails.

---

### Root Cause 5: Step3 planning is tool-routing, not architectural thinking

The three planning questions (forge.py ~lines 998–1011):
1. What is the user's real goal? Name the deliverable.
2. What facts are confirmed?
3. Which hat to activate?

These are routing decisions. They do not ask:
- What is the primary architectural risk or constraint in this request?
- What would cause this project to fail?
- What assumption needs validation before generating?

Archie plans which tool to call, not what approach is right. The expert
pre-action then receives a routing decision as its context, not a risk-aware
plan.

**Fix:** Add one sub-question to STEP 1: "What is the primary architectural risk
or constraint in this request?"

---

### Root Cause 6: No expert identity for conversational turns

When no hat is active (architecture discussion, follow-up questions, clarifying
turns), Archie's system prompt contains:
- Generic "be conversational, concise, and architect-level" instruction
- Tool contracts
- Routing rules

No OCI-specific expert framing. No pattern recognition instinct. No risk flags
enumerated. An SA asking "what's the best HA approach for a 3-tier web app?"
gets a helpful assistant response, not an expert architect response.

**Fix:** Add `_EXPERT_IDENTITY` constant that establishes OCI SA persona with
PATTERN RECOGNITION, RISK INSTINCT, SPECIFICITY, ASSUMPTION SURFACING, and
PROACTIVE GUIDANCE as named behaviors. Prepend to full system prompt.

---

## The Seven Changes

### p51a — hat_engine.py: inject missing sections

**File:** `agent/hat_engine.py`, `build_expert_block()` (lines 165–174)

```python
# Before
for section in (
    "Core Principles",
    "Quality Bar",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):

# After
for section in (
    "Core Principles",
    "Quality Bar",
    "Pre-Action Checklist",      # ← ADD
    "Post-Action Review",        # ← ADD
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

The two new sections go after Quality Bar so the LLM sees quality standards
before operational procedure.

**Impact:** When expert pre-action fires and says "List every unconfirmed
prerequisite from this hat's Pre-Action Checklist," the checklist is now actually
in context. Same for post-review's "Work through each item in your Post-Action
Review section." This single fix makes every subsequent expert call more
grounded and specific.

---

### p51b — forge.py: architectural judgment in expert pre-action

**File:** `skillforge/forge.py`, `_run_expert_pre_action()` (~lines 1164–1210)

Replace the `pre_action_prompt` string and update `_EXPERT_PRE_ACTION_HEADERS`.

**New prompt structure:**

```
╔══════════════════════════════════╗
║  STEP 4 — EXPERT PRE-ACTION      ║
╚══════════════════════════════════╝
You are wearing the [hat_label] hat. You ARE the expert.
Before calling 'tool_name', think as a senior OCI Solutions Architect...

KNOWN FACTS:
- [Every confirmed value from memory: shape, region, OCPU, memory, storage,
  HA mode, budget, compliance scope. Specific values only.]

GAPS:
- [Every unconfirmed item from this hat's Pre-Action Checklist.
  For each: state the DEFAULT and why. Only NEEDS_CLARIFICATION if unsafe.]

EXPERT ASSESSMENT:
- WORKLOAD PATTERN: [3-tier web / microservices / ML inference / data platform /
  batch / lift-and-shift / RAG pipeline / hybrid connectivity. 1-2 critical reqs.]
- RECOMMENDATION: [Exact OCI services, shapes, SKUs, quantities, topology tiers.]
- WHY THIS APPROACH: [One sentence: why this over the main alternative.
  Must cite a specific constraint from KNOWN FACTS or workload pattern.]
- TOP RISK: [Most likely failure mode. How you are mitigating it.]
- PROACTIVE FLAG: [One thing the customer hasn't asked but should know.
  "Note: single-AD assumed — if SLA > 99.9%, costs double for HA."
  If nothing relevant: None.]

SUB-AGENT TASK:
- [Complete, self-contained instruction for the sub-agent. All sizing, shapes,
  services, and constraints. The sub-agent has no other context.]
```

**Updated `_EXPERT_PRE_ACTION_HEADERS`:**
```python
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT TASK:",     # was SUB-AGENT INSTRUCTIONS:
)
```

The change from `SUB-AGENT INSTRUCTIONS:` to `SUB-AGENT TASK:` reinforces that
this is a directive to an execution specialist, not a set of instructions to
itself.

**Impact:** Every expert pre-action call now produces a named workload pattern,
a justified recommendation, an explicit risk, and a proactive customer flag.
The sub-agent receives a complete, self-contained task description instead of
whatever leaked through from EXPERT ASSESSMENT prose.

---

### p51c — forge.py: structured critic with Quality Bar

**File:** `skillforge/forge.py`, `_run_critique_pass()` (~lines 1487–1492)

**New `critic_prompt`:**

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

**Impact:** The Quality Bar in the `[ACTIVE EXPERT]` block (put there by p51a
and already present before) is now explicitly applied item by item. An approval
requires evidence of PASS for each item. A failure requires naming the exact
field and expected value — not vague concerns.

---

### p51d — forge.py: Phase D architectural soundness in post-review

**File:** `skillforge/forge.py`, `_run_expert_post_review()` (~lines 1344–1381)

Add Phase D block after Phase C and before FINAL DECISION in `review_prompt`:

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

Also raise `_EXPERT_REVIEW_MIN_CHARS` from 800 to 1000. The review now has 4
phases; the higher minimum ensures Phase D gets substantive content.

**Phase D findings mechanism:** When the expert post-review completes, its full
output (including Phase D) is appended to the prompt context before Archie
writes its final response to the user. When Phase D produces a SUGGEST, Archie
can reference it proactively: "BOM delivered. Note: your LB is public-facing and
WAF wasn't scoped — recommend adding it before production."

---

### p52a — forge.py: architectural risk in step3 planning

**File:** `skillforge/forge.py`, `_run_step3_planning()` (~lines 998–1011)

Add one sub-question to the end of STEP 1 in `planning_prompt`:

```python
"- What is the primary architectural risk or constraint in this request? "
"(HA exposure, compliance requirement, budget ceiling, migration complexity, "
"public ingress without filtering, data sensitivity). Name it — do not skip.\n"
```

This is a sub-question within STEP 1, not a new required header. The
`_STEP3_PLANNING_HEADERS` validation tuple is unchanged.

**Impact:** Every planning call produces a named risk statement before tool
selection. The expert pre-action then operates with risk context already
established, not starting from scratch.

---

### p52b — archie_wiring.py: expert identity for all turns

**File:** `agent/archie_wiring.py`

Add `_EXPERT_IDENTITY` constant (after imports, before `_TOOL_SEQUENCING_RULES`)
and prepend it to `full_prompt` in `build_forge()`.

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

In `build_forge()`, update `full_prompt`:

```python
# Before
full_prompt = (
    routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
).strip()

# After
full_prompt = (
    _EXPERT_IDENTITY + "\n\n" + routing_guidance + "\n\n" + base_system_prompt
    + "\n\n" + _TOOL_SEQUENCING_RULES
).strip()
```

`_EXPERT_IDENTITY` goes first to establish persona before tool routing rules.

---

### p52c — archie_memory_impl.py: enrich memory injection

**File:** `agent/archie_memory_impl.py`, `ArchiePromptEnricher.__call__()`
(lines ~80–97)

After the existing `constraints` injection block, add:

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

`MemorySnapshot.facts` already carries these fields when populated. The expert
KNOWN FACTS section in pre-action currently operates on a partial view of session
knowledge. With this change, confirmed infrastructure details and previously
answered questions are visible to the expert without requiring redundant
re-confirmation.

---

## Files Changed

| Task | File | Type of change | Lines |
|---|---|---|---|
| p51a | `agent/hat_engine.py` | +2 section names in tuple | ~2 |
| p51b | `skillforge/forge.py` | Replace `pre_action_prompt`; update `_EXPERT_PRE_ACTION_HEADERS` | ~35 |
| p51c | `skillforge/forge.py` | Replace `critic_prompt` | ~15 |
| p51d | `skillforge/forge.py` | Add Phase D block to `review_prompt`; raise `_EXPERT_REVIEW_MIN_CHARS` | ~14 |
| p52a | `skillforge/forge.py` | Add one sub-question to step3 STEP 1 | ~4 |
| p52b | `agent/archie_wiring.py` | Add `_EXPERT_IDENTITY`; update `full_prompt` assembly | ~30 |
| p52c | `agent/archie_memory_impl.py` | Add two memory field injections | ~10 |

---

## Run Order

```
p51a (prerequisite)
  │
  ├─ p51b (same file as p51c/p51d — recommend single PR for all three)
  ├─ p51c
  └─ p51d
       │
       ├─ p52a
       ├─ p52b
       └─ p52c
```

p51a is the prerequisite because p51b and p51c reference sections that p51a
makes visible in the expert context. They are safe to implement without p51a,
but they reach their full potential only after the hat sections are injected.

p51b, p51c, p51d all touch `skillforge/forge.py` — recommend a single branch
`claude/p51-forge` for all three to avoid merge conflicts.

p52a, p52b, p52c touch different files and can be independent branches.

---

## Expected Experience After These Changes

### Before (current behavior)

**User:** "Generate a BOM for a web application"

**Archie pre-action output:**
```
KNOWN FACTS: region us-chicago-1, shape E5.Flex, customer Acme Corp.
GAPS: OCPU count not confirmed — defaulting to 4.
EXPERT ASSESSMENT: Use E5.Flex with 4 OCPU and 32 GB memory.
SUB-AGENT INSTRUCTIONS: Generate BOM for web application with E5.Flex.
```

**Archie final response:** "Here's your BOM."

---

### After p51/p52

**Archie pre-action output:**
```
KNOWN FACTS: customer Acme Corp, region us-chicago-1, shape E5.Flex,
  no HA confirmed, no budget stated, no compliance scope stated.
GAPS:
  - OCPU count: defaulting to 4 OCPU/server (standard 3-tier web tier sizing)
  - Memory: defaulting to 32 GB/server (8 GB/OCPU E5.Flex ratio)
  - HA mode: defaulting to single-AD (customer can revise to multi-AD)
  - Storage: defaulting to 500 GB Block Volume Balanced tier per app server
EXPERT ASSESSMENT:
  - WORKLOAD PATTERN: 3-tier web application. Critical requirements: horizontal
    scalability for web tier, data persistence for DB tier, public ingress handling.
  - RECOMMENDATION: 2× VM.Standard.E5.Flex (4 OCPU, 32 GB) app tier, 1×
    VM.Standard.E5.Flex (4 OCPU, 32 GB) DB tier, OCI LB 100 Mbps. Shapes:
    B97384/B97385. Storage: 500 GB Block Volume Balanced (B91961) per server.
  - WHY THIS APPROACH: E5.Flex over X9 — this workload has no Intel-binary
    dependency and E5 delivers the same throughput at 25% lower OCPU cost.
  - TOP RISK: Single-AD deployment means a fault domain failure affects all
    tiers simultaneously. Noting the assumption in BOM so customer can evaluate.
  - PROACTIVE FLAG: Note: WAF not scoped but a public-facing load balancer is
    present. Public API without ingress filtering is a P1 security gap — recommend
    adding OCI WAF as a follow-up or in a revised BOM.
SUB-AGENT TASK: Generate BOM for 2× VM.Standard.E5.Flex app servers (4 OCPU,
  32 GB each, B97384/B97385) + 1× VM.Standard.E5.Flex DB server (4 OCPU, 32 GB)
  + OCI LB 100 Mbps + 500 GB Block Volume Balanced per server. Region
  us-chicago-1. Single-AD. Assumptions: OCPU=4, memory=32 GB, storage=500 GB BV,
  single-AD, no WAF, no managed DB service.
```

**Post-review Phase D:**
```
PHASE D — Architectural soundness:
- GOAL FIT: YES — BOM covers the stated 3-tier web workload.
- ANTIPATTERNS: FLAG: No WAF line item despite public-facing load balancer.
  Public API without ingress filtering is a P1 security exposure.
- NEXT STEP FLAG: SUGGEST: Generate architecture diagram to validate tier
  topology and confirm LB → app → DB path before Terraform. Add WAF to scope.
```

**Archie final response:**
> "Here's your BOM for the 3-tier web app — $847/month for 3× E5.Flex VMs,
> OCI LB, and block storage.
>
> Two things to note: I assumed single-AD deployment (no HA multiplier) and no
> WAF in scope. Your load balancer is public-facing, so I'd recommend adding OCI
> WAF before production — it's a straightforward addition to the BOM. Want me
> to revise it with WAF included, or generate the architecture diagram first so
> we can validate the topology?"

---

## What This Does NOT Address

- **BOM sub-agent LLM reasoning.** The BOM sub-agent is a deterministic Python
  regex extraction pipeline, not an LLM. Expert hat reasoning influences it only
  through the parseable `[SUB-AGENT INSTRUCTIONS]` sizing block (addressed in
  p50a). Making the BOM sub-agent itself LLM-driven is a Phase 6+ decision.

- **Governor as hard code-enforced blocker.** The governor currently runs as a
  hat with soft blocking. Elevating it to a hard code-enforced gate in Forge
  (before tools fire) is a separate Forge core change.

- **Cross-hat synthesis.** Ensuring BOM, Diagram, WAF, and Terraform are
  architecturally consistent with each other (e.g., WAF references the same
  VCN topology as the diagram) is a future capability requiring a synthesis
  layer above individual hats.

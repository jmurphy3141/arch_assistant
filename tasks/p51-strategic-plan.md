# p51/p52 Strategic Plan — Expert Voice, Not Form-Filling

**Date:** 2026-05-18  
**Supersedes:** tasks/p51-series-updated.md  
**Constraint:** `skillforge/forge.py` — zero OCI/Archie vocabulary. Domain expertise lives in hats.

---

## The Core Insight

The previous plan replaced one form with a better form. That is not the goal.

A senior architect doesn't fill out assessment templates. When they pre-think a BOM task they
say: *"This is a standard 3-tier web workload. I'm defaulting to E5.Flex at 4 OCPU — safe
starting point, and they didn't give me a shape. HA mode isn't confirmed so I'm assuming
single-AD, flagging it in assumptions. Real risk here is they have a public LB but no WAF scope
— I'll surface that after delivery. Here's the sub-agent brief."*

That is expert voice. Our EXPERT ASSESSMENT with five labeled sub-bullets produces:
*"DOMAIN PATTERN: 3-tier web. RECOMMENDATION: E5.Flex. WHY THIS APPROACH: lower cost."*
That is form-filling. It is not the same thing.

**The shift:** Turn EXPERT REASONING into a prose block that covers four things naturally
(pattern, recommendation + defaults, risk, proactive insight) without requiring labeled
sub-bullets. The structure enforces that the section exists and is substantive — not that
each element has its own label.

---

## Two Bugs Found in the Previous Plan

### Bug 1 — Critic's `## Per-Tool Validation Schema` is never injected

`build_expert_block()` iterates these section names:
```
Core Principles, Quality Bar, Pre-Action Checklist, Post-Action Review,
Output Contract, Critic Evaluation Guidance, Failure Questions
```

The critic hat has NO `## Quality Bar` section. It has `## Per-Tool Validation Schema` —
with exact field checks per tool (artifact_key required, B-number SKUs, math verification, etc.).
This section is invisible to every critic call. The critic literally cannot apply its own schema.

**Fix (p51c-a):** Add `"Per-Tool Validation Schema"` to the injection list in `build_expert_block()`.
This is a 1-line change. It affects only the critic hat (other hats don't have this section).

The critic prompt can then say: **"Apply your Per-Tool Validation Schema"** and actually get
the schema.

### Bug 2 — Phase D placed after FINAL DECISION breaks decision routing

`_run_expert_post_review()` reads the decision on `lines[-1]` — the last non-empty line.
The previous plan appended Phase D after the FINAL DECISION line, making Phase D's last line
the one the router reads. This silently breaks approve/iterate/surface routing.

**Fix (p51d):** Phase D goes **before** the FINAL DECISION block. The prompt must close with
`"The FINAL DECISION line must be the very last line of your response."`

---

## Priority Order

```
p51a  ── hat_engine.py      2 lines    BLOCKER for p51b and p51c
         Inject Pre-Action Checklist + Post-Action Review

p51b  ── forge.py           ~20 lines  HIGHEST IMPACT
         Replace 5-label form with EXPERT REASONING prose block

p51c  ── hat_engine.py +    ~5 lines   HIGH IMPACT (fixes silent bug)
         forge.py           ~10 lines
         Inject Per-Tool Validation Schema; structured critic prompt

p51d  ── forge.py           ~15 lines  HIGH IMPACT (Phase D advisory)
         Add Phase D before FINAL DECISION; raise min chars to 1000

p52c  ── archie_memory_     ~20 lines  HIGH IMPACT (Phase C was blind)
         impl.py
         Populate MemorySnapshot.formatted

p52a  ── forge.py           ~3 lines   MEDIUM (risk question in step3)
p52b  ── archie_wiring.py   ~30 lines  MEDIUM (OCI expert identity)
```

**PR grouping:**
- PR 1: p51a (hat_engine.py — unblocks everything, 2 lines)
- PR 2: p51b + p51c + p51d (all forge.py changes — review together)
- PR 3: p52a + p52b + p52c (independent files — can merge together)

---

## p51a — Inject Pre-Action Checklist, Post-Action Review, and Per-Tool Validation Schema

**File:** `agent/hat_engine.py`, `build_expert_block()` (line ~164)

This is a 3-line change (not 2 — the previous plan missed Per-Tool Validation Schema).

```python
# BEFORE
for section in (
    "Core Principles",
    "Quality Bar",
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):

# AFTER
for section in (
    "Core Principles",
    "Quality Bar",
    "Per-Tool Validation Schema",   # ← critic hat's domain-specific checks
    "Pre-Action Checklist",         # ← all domain hats
    "Post-Action Review",           # ← all domain hats
    "Output Contract",
    "Critic Evaluation Guidance",
    "Failure Questions",
):
```

**Verification:**
```bash
python3.11 -m compileall agent/hat_engine.py -q

python3.11 -c "
from agent.hat_engine import build_expert_block

# Domain hat: must have Pre-Action Checklist and Post-Action Review
bom = build_expert_block('oci_bom_expert')
assert 'Pre-Action Checklist' in bom, 'FAIL: Pre-Action Checklist missing from BOM hat'
assert 'Post-Action Review' in bom, 'FAIL: Post-Action Review missing from BOM hat'
assert 'Per-Tool Validation Schema' not in bom, 'FAIL: BOM hat has no validation schema'

# Critic hat: must have Per-Tool Validation Schema
critic = build_expert_block('critic')
assert 'Per-Tool Validation Schema' in critic, 'FAIL: critic schema not injected'
assert 'artifact_key' in critic, 'FAIL: critic schema content missing'

print('PASS — all section injection checks pass')
"

pytest tests/test_hat_engine.py -q --tb=short -m "not live"
```

**Acceptance criteria:**
- `build_expert_block('critic')` contains the full Per-Tool Validation Schema (with B-number, artifact_key, and math verification content).
- `build_expert_block('oci_bom_expert')` contains Pre-Action Checklist and Post-Action Review.
- No other hat is affected adversely (sections not present are silently skipped).

---

## p51b — Expert pre-action: prose reasoning, not labeled form

**File:** `skillforge/forge.py`

### The design decision

Replace the 5-label EXPERT ASSESSMENT sub-form with a single `EXPERT REASONING:` prose block.
The four required elements (pattern, recommendation + defaults, risk, proactive) become guiding
questions in the prompt text — not mandatory output labels. This licenses expert voice.

The enforcement mechanism remains: section-header guard checks for `EXPERT REASONING:`, and
the shallow-response guard (600 chars) catches inadequate depth. We lose per-element header
enforcement but gain genuine expert output.

### Change 1 — `_EXPERT_PRE_ACTION_HEADERS` (lines 67–72)

```python
# BEFORE (4 sections — GAPS and EXPERT ASSESSMENT are separate)
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT INSTRUCTIONS:",
)

# AFTER (3 sections — GAPS merged into EXPERT REASONING)
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "EXPERT REASONING:",
    "SUB-AGENT TASK:",
)
```

### Change 2 — `pre_action_prompt` string (lines 1170–1191)

```python
pre_action_prompt = (
    f"{prompt}{retry_context}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
    "╚══════════════════════════════════╝\n"
    f"You are wearing the [{hat_label}] hat. You ARE the expert — "
    "not a structured router, a senior expert who has seen this type of request before.\n\n"
    "KNOWN FACTS:\n"
    "Every confirmed value from memory and conversation. Specific values only — "
    "region, sizing, shapes, HA mode, budget, compliance scope. "
    "Do not list anything unconfirmed here.\n\n"
    "EXPERT REASONING:\n"
    f"Think through calling '{tool_name}' as the expert this hat defines. "
    "Cover these four things naturally — no sub-labels required:\n"
    "What type of request is this and what pattern does it follow? "
    "What is your recommendation, and for anything not in KNOWN FACTS, "
    "what default will you use and why is it safe? "
    "What is the primary risk for this deliverable and how are you handling it? "
    "Is there one thing the requester has not asked but should know? "
    "Write as an expert, not as a form. "
    "If a default is architecturally unsafe to make, stop here and write only: "
    "NEEDS_CLARIFICATION: <one focused question>\n\n"
    "SUB-AGENT TASK:\n"
    "The complete, self-contained task brief for the sub-agent. "
    "Include every confirmed value from KNOWN FACTS and every default from EXPERT REASONING. "
    "The sub-agent has no other context — do not reference 'the above' or 'as discussed'."
)
```

### Change 3 — Update header retry message (line ~1251)

```python
# BEFORE
"KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT INSTRUCTIONS:."

# AFTER
"KNOWN FACTS:, EXPERT REASONING:, SUB-AGENT TASK:."
```

**Verification:**
```bash
python3.11 -m compileall skillforge/forge.py -q

# Required: new section names present
grep "EXPERT REASONING" skillforge/forge.py | wc -l   # expect ≥ 2 (headers + prompt)
grep "SUB-AGENT TASK" skillforge/forge.py | wc -l     # expect ≥ 2 (headers + prompt)

# Forbidden: old names gone
grep "GAPS:" skillforge/forge.py | grep -v "#"        # must be empty
grep "EXPERT ASSESSMENT" skillforge/forge.py           # must be empty
grep "SUB-AGENT INSTRUCTIONS" skillforge/forge.py      # must be empty

# Forbidden: no OCI vocabulary in forge.py
grep -i "\bOCI\b\|E5\.Flex\|us-chicago\|3-tier\|microservice" skillforge/forge.py

pytest tests/test_forge.py -q --tb=short -m "not live"
```

**Acceptance criteria — quality, not just structure:**
- EXPERT REASONING is prose, not sub-bullets. The prompt says "no sub-labels required."
- Expert voice is enabled by: "You ARE the expert — not a structured router."
- All four elements (pattern, defaults, risk, proactive) are covered in the framing text but
  not required as separate labeled outputs.
- SUB-AGENT TASK explicitly forbids "as discussed" / "see above" references.
- Zero OCI/Archie vocabulary in `skillforge/forge.py`.

---

## p51c — Critic: apply the actual validation schema, not an abstract Quality Bar

**Files:** `agent/hat_engine.py` (done in p51a), `skillforge/forge.py`

### Why this is the right fix

The critic hat has a `## Per-Tool Validation Schema` with exact, tool-specific field checks:
- BOM: requires B-number SKUs, artifact_key, math verification (quantity × price × 730 ± 1%)
- Diagram: requires artifact_key, node_count > 0, OCI icons for all services
- Terraform: requires main.tf / variables.tf / outputs.tf, no literal OCIDs, no prose lines
- WAF/POV/JEP: requires all sections present

After p51a, this schema is in the critic's system message. The critic prompt must now tell
the LLM to apply it.

### Change — Replace `critic_prompt` (lines 1487–1492)

```python
# BEFORE (3 lines — rubber stamp)
critic_prompt = (
    f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
    f"Review the result of '{tool_name}' above.\n"
    f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
    f"If you have concerns, describe them as plain text."
)

# AFTER
critic_prompt = (
    f"{prompt}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  CRITIC REVIEW                   ║\n"
    "╚══════════════════════════════════╝\n"
    f"Apply your Per-Tool Validation Schema and Critic Evaluation Guidance "
    f"to the result of '{tool_name}'. Do not rubber-stamp.\n\n"
    f"For each check in your schema: PASS or FAIL with the specific field or value.\n\n"
    "Final line — ONLY one of:\n"
    f"  {{\"tool\": \"critic_approve\", \"args\": {{}}}}   — every check passes\n"
    "  <exact failing check as plain text — field name and what was wrong>   "
    "— if anything fails\n\n"
    "One failing check is enough to reject. Name the field, not a vague concern."
)
```

**Verification:**
```bash
python3.11 -m compileall skillforge/forge.py -q
grep "Per-Tool Validation Schema" skillforge/forge.py   # must appear in critic prompt
grep "rubber-stamp" skillforge/forge.py | wc -l         # must be ≥ 1
pytest tests/test_forge_critique.py -q --tb=short -m "not live"
```

**Acceptance criteria:**
- Critic prompt references "Per-Tool Validation Schema" (not abstract "Quality Bar").
- PASS/FAIL per check is required before approve/reject.
- One failure is sufficient to reject — prompt says so explicitly.
- critic_approve is the only tool the critic can call.

---

## p51d — Phase D: Expert reflection before the decision line

**File:** `skillforge/forge.py`, `_run_expert_post_review()`, `review_prompt`

Phase D asks: *Is this output genuinely right for this request?* It is advisory — it does not
change approve/iterate/surface routing. It surfaces structural concerns and next-step suggestions
that Archie can use in its final response to the user.

**Critical constraint:** Phase D must go **before** the FINAL DECISION block. Decision routing
reads `lines[-1]`. If Phase D appears after the decision, routing breaks silently.

### Change 1 — Raise `_EXPERT_REVIEW_MIN_CHARS` (line 78)

```python
_EXPERT_REVIEW_MIN_CHARS = 1000   # was 800 — four phases require more
```

### Change 2 — Add Phase D and update FINAL DECISION wording

In `review_prompt`, replace:
```python
"FINAL DECISION — after completing Phases A, B, and C, output EXACTLY ONE line:\n"
f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
f"  {_EXPERT_REVIEW_ITERATE}: <issue>    — at least one fixable FAIL or CONFLICT\n"
f"  {_EXPERT_REVIEW_SURFACE}: <issue>    — unfixable gap requiring user clarification\n\n"
"You MUST complete all three phases before writing the final decision line.\n"
"Do NOT call a tool here."
```

With:
```python
"PHASE D — Expert reflection (advisory):\n"
"Forget the per-item checks for a moment. Is this output genuinely right for this request? "
"In 2–3 sentences: note any structural concern not caught by Phases A–C "
"(a missing design element, an obvious next gap, a risk the customer has not asked about). "
"Then name one concrete next step the requester has not asked about. "
"If neither applies, say so briefly. "
"This section does not change the FINAL DECISION.\n\n"
"FINAL DECISION — after completing all four phases, write this as the very last line:\n"
f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
f"  {_EXPERT_REVIEW_ITERATE}: <issue>    — at least one fixable FAIL or CONFLICT\n"
f"  {_EXPERT_REVIEW_SURFACE}: <issue>    — unfixable gap requiring user clarification\n\n"
"The FINAL DECISION line must be the very last line of your response. "
"Do NOT call a tool here."
```

**Verification:**
```bash
python3.11 -m compileall skillforge/forge.py -q
grep "_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py   # must show 1000
grep "Phase D" skillforge/forge.py | wc -l            # must be ≥ 1
grep "very last line" skillforge/forge.py             # must appear (routing safety)

# Confirm decision routing reads lines[-1] — no code change needed there
grep "lines\[-1\]" skillforge/forge.py                # must still be present

pytest tests/test_forge.py -q --tb=short -m "not live"
```

**Acceptance criteria:**
- Phase D is 2–3 sentences of prose — no labeled sub-bullets (GOAL FIT / ANTIPATTERNS removed).
- Phase D appears BEFORE the FINAL DECISION block in the prompt string.
- Decision routing code (`lines[-1]`) unchanged — no routing regression.
- `_EXPERT_REVIEW_MIN_CHARS` is 1000.

---

## p52a — Risk question in step3 planning

**File:** `skillforge/forge.py`, `_run_step3_planning()`, `planning_prompt` (line ~994)

One sentence added to STEP 1. Forces the planner to name the primary risk before choosing
a hat or plan. Domain-agnostic framing — specific risk vocabulary lives in hats and Archie.

```python
# Current STEP 1 block — add one sentence at the end:
"STEP 1 — UNDERSTAND:\n"
"- What is the user's real goal? Name the deliverable "
"(BOM, diagram, Terraform, POV, JEP, WAF review, or none).\n"
"- Is this a new request, a revision, or a clarification?\n"
"- Is anything ambiguous? If so, what is missing?\n"
"- What is the primary risk to delivering this well — a missing input, a structural "
"choice that is hard to change later, or a scope question that needs confirmation now?\n\n"
```

`_STEP3_PLANNING_HEADERS` is unchanged — the new sentence is within STEP 1, not a new section.

**Verification:**
```bash
python3.11 -m compileall skillforge/forge.py -q
grep "primary risk to delivering" skillforge/forge.py   # must appear
grep "STEP 1 — UNDERSTAND" skillforge/forge.py          # must still be present
```

---

## p52b — Expert identity in archie_wiring.py (OCI-specific, correct file)

**File:** `agent/archie_wiring.py`  
**Note:** OCI vocabulary is appropriate here — this is Archie code, not Forge.

### Change 1 — Add `_EXPERT_IDENTITY` constant

Add after the `_TOOL_SEQUENCING_RULES` definition:

```python
_EXPERT_IDENTITY = """\
## Expert Identity

You are a senior OCI Solutions Architect. Think as this expert in every response —
tool calls, post-action replies, and conversational turns.

**Pattern recognition:** Name the architecture pattern at the start of any expert
reasoning — 3-tier web, microservices, ML inference, data platform, batch pipeline,
lift-and-shift, RAG, hybrid connectivity. The pattern determines which services and
risks apply.

**Risk instinct:** Surface the primary risk before it becomes the customer's problem.
Flag unprompted: public ingress without WAF, DB in a public subnet, no HA for a
production workload, GPU costs without budget confirmation, Terraform without compartment
OCID strategy, on-prem connectivity needs without DRG or FastConnect scope.

**Specificity:** Name the service, shape, and SKU. "VM.Standard.E5.Flex, 4 OCPU,
B97384 at $0.03/OCPU-hr" — not "a standard compute instance." Vague recommendations
are not SA-quality.

**Assumption transparency:** When you default a value, name it every time.
"Assuming us-chicago-1, single-AD, E5.Flex — confirm if your requirements differ."
Silent assumptions become production failures.

**Proactive next step:** After any artifact delivery, name the natural next step.
"BOM delivered. Before Terraform: generate the diagram to validate topology.
You have a public LB — recommend WAF before production."
"""
```

### Change 2 — Inject into `full_prompt` in `build_forge()`

```python
# BEFORE (line 128)
full_prompt = (
    routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
).strip()

# AFTER — expert identity goes between routing guidance and Archie system prompt
full_prompt = (
    routing_guidance + "\n\n"
    + _EXPERT_IDENTITY + "\n\n"
    + base_system_prompt + "\n\n"
    + _TOOL_SEQUENCING_RULES
).strip()
```

**Verification:**
```bash
python3.11 -m compileall agent/archie_wiring.py -q

python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_wiring import build_forge
from agent.persistence_objectstore import InMemoryObjectStore
import asyncio

store = InMemoryObjectStore()
async def dummy(p, s, l): return ''
forge = build_forge(store=store, customer_id='t', customer_name='T', text_runner=dummy)
sp = forge._get_system_msg()
assert 'Pattern recognition' in sp, 'FAIL: Expert identity not in system prompt'
assert 'Risk instinct' in sp
assert 'Assumption transparency' in sp
print('PASS:', len(sp), 'chars')
"

pytest tests/test_archie_forge_wiring.py tests/test_archie_wiring.py -q --tb=short -m "not live"
```

**Acceptance criteria:**
- `_EXPERT_IDENTITY` defined in `agent/archie_wiring.py` — not in `skillforge/`.
- Injected between routing_guidance and base_system_prompt.
- OCI vocabulary (E5.Flex, WAF, DRG, etc.) stays in `agent/` files only.

---

## p52c — Populate `MemorySnapshot.formatted` (Phase C was blind)

**File:** `agent/archie_memory_impl.py`, `ArchieMemory.assemble()` (lines 36–62)

`MemorySnapshot.formatted` defaults to `""`. `_run_expert_post_review()` uses it for Phase C
memory consistency check — but it gets an empty string, so Phase C checks against nothing.
This is why Phase C has never caught a wrong region, wrong shape, or wrong HA mode.

### Change — Build `formatted` before the `return MemorySnapshot(...)` call

```python
# Add after the resolved_questions block, before return:

# Prompt-ready text for Phase C memory consistency check.
_fmt: list[str] = []
if facts_summary:
    _fmt.append(f"Customer facts: {facts_summary}")

if isinstance(infrastructure_profile, dict) and infrastructure_profile:
    _profile = [
        f"  {k}: {v}"
        for k, v in sorted(infrastructure_profile.items())
        if v not in (None, "", [], {})
    ]
    if _profile:
        _fmt.append("Infrastructure profile:\n" + "\n".join(_profile))

if constraints:
    _clist = [
        f"  {k}: {v}"
        for k, v in sorted(constraints.items())
        if v not in (None, "", [], {})
    ]
    if _clist:
        _fmt.append("Approved constraints:\n" + "\n".join(_clist))

if isinstance(resolved_questions, list) and resolved_questions:
    _recent = [str(q) for q in resolved_questions[-3:] if q]
    if _recent:
        _fmt.append("Recently resolved: " + "; ".join(_recent))

formatted = "\n".join(_fmt)

return MemorySnapshot(
    session_id=session_id,
    facts=facts,
    constraints=constraints,
    prior_artifacts=_prior_artifacts(context),
    decision_context=dict(context.get("latest_decision_context") or {}),
    raw=context,
    formatted=formatted,          # ← was always ""
)
```

**Verification:**
```bash
python3.11 -m compileall agent/archie_memory_impl.py -q

python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import InMemoryObjectStore

mem = ArchieMemory(InMemoryObjectStore())
ctx = {
    'archie': {
        'facts_summary': 'E5.Flex 4 OCPU, us-chicago-1, single-AD',
        'infrastructure_profile': {'region': 'us-chicago-1', 'shape': 'E5.Flex', 'ha': 'single-AD'},
        'latest_approved_constraints': {'budget': '5000/month'},
        'resolved_questions': ['region confirmed', 'shape confirmed as E5.Flex'],
    }
}
snap = mem.assemble(session_id='t', context=ctx, user_message='test')
assert snap.formatted, 'FAIL: formatted is empty'
assert 'E5.Flex' in snap.formatted
assert 'us-chicago-1' in snap.formatted
assert 'budget' in snap.formatted
assert 'region confirmed' in snap.formatted
print('PASS')
print(snap.formatted)
"

pytest tests/test_archie_memory_impl.py -q --tb=short -m "not live" 2>&1 | tail -5
```

**Acceptance criteria:**
- `formatted` is non-empty when context has any of: facts_summary, infrastructure_profile,
  constraints, resolved_questions.
- Values of `None`, `""`, `[]`, `{}` are filtered out.
- At most 3 resolved questions (avoid context bloat).
- Phase C in post-review now has real ground truth for consistency checking.

---

## What Each Change Delivers to the User

| Change | What Archie does differently after |
|--------|-------------------------------------|
| p51a | Pre-action checklist visible to expert; critic schema visible to critic |
| p51b | Expert reasoning reads as a senior architect's assessment, not a labeled form |
| p51c | Critic applies exact tool-specific checks — artifact_key, math, B-numbers — not abstract quality |
| p51d | After post-review, Archie surfaces structural concerns and next steps proactively |
| p52a | Planning step names the primary risk before choosing a hat |
| p52b | Every conversational turn starts with pattern recognition and risk instinct |
| p52c | Phase C can catch wrong region, wrong shape, wrong HA mode in sub-agent output |

---

## What This Does NOT Address

- Sub-agent LLM reasoning (BOM pipeline is deterministic — separate track)
- Hat file content quality (hats are already expert-grade — no changes needed)
- Cross-artifact consistency (BOM ↔ diagram ↔ Terraform — future work)
- `archie_session.py` routing (separate concern — see bug fix already committed)

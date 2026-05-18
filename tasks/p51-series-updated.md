# p51 Series — Updated Engineering Manager Prompts

**Date:** 2026-05-18  
**Status:** p51a complete (in main). p51b in progress. p51c–p52c planned here.

**Non-negotiable constraint:** `skillforge/forge.py` must remain **manager-agnostic**.  
No Archie, OCI, or cloud-provider language inside `skillforge/`. Domain expertise lives in hats and `agent/`.

---

## Architecture of the Expert Reasoning Loop

```
run_turn()
  ├── step3_planning        → "STEP 3 — PLANNING" (forge.py)      ← p52a
  ├── expert_pre_action     → "STEP 4 — EXPERT PRE-ACTION"         ← p51b
  │    hat provides: Pre-Action Checklist (injected via p51a)
  ├── tool dispatch
  ├── expert_post_review    → "STEP 6 — EXPERT POST-REVIEW"        ← p51d
  │    Phase A: Quality Bar · Phase B: Post-Action Review · Phase C: Memory · Phase D (new)
  └── critique_pass         → "CRITIC REVIEW"                      ← p51c
```

The hat's **Pre-Action Checklist** and **Post-Action Review** sections give the domain-specific
content. Forge's prompts are structural containers only — they reference "this hat's section" but
contain no domain vocabulary.

---

## p51a — Inject Pre-Action Checklist + Post-Action Review into expert block

**File:** `agent/hat_engine.py`, `build_expert_block()` (line 164)

**Status:** Merge to main before p51b.

**Change:** Two lines added to the section iteration list:

```python
for section in (
    "Core Principles",
    "Quality Bar",
    "Pre-Action Checklist",      # ← ADD (p51a)
    "Post-Action Review",        # ← ADD (p51a)
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
b = build_expert_block('oci_bom_expert')
assert 'Pre-Action Checklist' in b, 'FAIL: Pre-Action Checklist missing'
assert 'Post-Action Review' in b, 'FAIL: Post-Action Review missing'
print('PASS', len(b), 'chars')
"
pytest tests/test_hat_engine.py -q --tb=short
```

---

## p51b — Expert pre-action: domain-agnostic architectural judgment

**File:** `skillforge/forge.py`

**⚠ Constraint:** No OCI/Archie vocabulary in this file. Domain content comes from the hat's
Pre-Action Checklist (injected via p51a). The sub-sections are universal expert reasoning steps.

### Change 1 — Update `_EXPERT_PRE_ACTION_HEADERS` (line 67–72)

```python
# BEFORE
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT INSTRUCTIONS:",
)

# AFTER
_EXPERT_PRE_ACTION_HEADERS = (
    "KNOWN FACTS:",
    "GAPS:",
    "EXPERT ASSESSMENT:",
    "SUB-AGENT TASK:",
)
```

### Change 2 — Replace `pre_action_prompt` string (lines 1170–1191)

```python
pre_action_prompt = (
    f"{prompt}{retry_context}\n\n"
    "╔══════════════════════════════════╗\n"
    "║  STEP 4 — EXPERT PRE-ACTION      ║\n"
    "╚══════════════════════════════════╝\n"
    f"You are wearing the [{hat_label}] hat. You ARE the expert.\n"
    f"Before calling '{tool_name}', think as the senior expert this hat represents. "
    "Use EXACTLY this structure:\n\n"
    "KNOWN FACTS:\n"
    "- [Every confirmed value from memory and conversation: shapes, region, sizing, "
    "HA mode, budget, compliance scope, customer name. Specific values only — no vague summaries.]\n\n"
    "GAPS:\n"
    "- [Every unconfirmed item from this hat's ## Pre-Action Checklist. "
    "For each gap: state the default you will use and WHY it is safe to default. "
    "Only flag NEEDS_CLARIFICATION if a default is architecturally unsafe.]\n\n"
    "EXPERT ASSESSMENT:\n"
    "- DOMAIN PATTERN: [Name the type of system or problem being requested. "
    "Be precise — not 'general' or 'standard'. "
    "This pattern determines which services and risks apply.]\n"
    "- RECOMMENDATION: [Exact solution with specifics — name the specific service, "
    "shape, SKU, module, or topology. Never generic advice.]\n"
    "- WHY THIS APPROACH: [One sentence: why this approach over the primary alternative. "
    "Must reference a specific constraint from KNOWN FACTS or the domain pattern.]\n"
    "- TOP RISK: [The most likely failure mode for this deliverable. "
    "How you are mitigating it in the sub-agent task below.]\n"
    "- PROACTIVE FLAG: [One thing the requester should know that they have not asked. "
    "Write: SUGGEST: <specific concern or next step>. "
    "If genuinely nothing to flag: None.]\n\n"
    "SUB-AGENT TASK:\n"
    "- [Complete, self-contained task instruction for the sub-agent. "
    "Include all sizing, shapes, services, and constraints from KNOWN FACTS and "
    "your GAPS defaults. The sub-agent has no other context — this must be fully specified.]\n\n"
    "Do NOT call a tool here. "
    "If a GAPS item is architecturally unsafe to default, output only: "
    "NEEDS_CLARIFICATION: <one focused question>"
)
```

### Change 3 — Update header retry message (line 1251)

```python
# BEFORE
"KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT INSTRUCTIONS:. "
# AFTER
"KNOWN FACTS:, GAPS:, EXPERT ASSESSMENT:, SUB-AGENT TASK:. "
```

### Verification

```bash
python3.11 -m compileall skillforge/forge.py -q
grep "DOMAIN PATTERN" skillforge/forge.py
grep "PROACTIVE FLAG" skillforge/forge.py
grep "WHY THIS APPROACH" skillforge/forge.py
# Must appear in both headers tuple AND prompt:
grep "SUB-AGENT TASK" skillforge/forge.py | wc -l   # expect >= 2
# Must NOT appear (OCI-specific language forbidden in forge.py):
grep -i "OCI\|E5\.Flex\|us-chicago\|3-tier\|microservice\|lift-and-shift" skillforge/forge.py | grep -v "^Binary"
pytest tests/test_forge.py tests/test_forge_critique.py -q --tb=short -m "not live"
```

### Acceptance Criteria

- `_EXPERT_PRE_ACTION_HEADERS` tuple matches prompt section labels exactly.
- `pre_action_prompt` contains no OCI, cloud-provider, or Archie-specific vocabulary.
- All five EXPERT ASSESSMENT sub-labels present: DOMAIN PATTERN, RECOMMENDATION, WHY THIS APPROACH, TOP RISK, PROACTIVE FLAG.
- SUB-AGENT TASK replaces SUB-AGENT INSTRUCTIONS everywhere in forge.py.
- Existing tests pass.

---

## p51c — Structured critic: per-item Quality Bar, no rubber-stamping

**File:** `skillforge/forge.py`, `_run_critique_pass()` (lines 1487–1492)

The current critic prompt is 3 lines. It never tells the LLM to apply the Quality Bar. An empty
`{}` JSON object is enough to approve. This change requires item-by-item PASS/FAIL evidence before
the critic can approve.

### Change — Replace `critic_prompt` (lines 1487–1492)

```python
# BEFORE (3 lines)
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
    f"You are reviewing the result of '{tool_name}'. "
    "You are NOT rubber-stamping — apply your ## Quality Bar honestly.\n\n"
    "For each item in your ## Quality Bar section write:\n"
    "  PASS: <brief evidence>   — if the item is satisfied\n"
    "  FAIL: <exact field or value that is wrong>   — if not\n\n"
    "After all Quality Bar items, write EXACTLY ONE final line:\n"
    f"  {{\"tool\": \"critic_approve\", \"args\": {{}}}}   "
    "— only if every item is PASS\n"
    "  <plain-text: first failing item — exact field name and what was wrong>   "
    "— if any item fails\n\n"
    "Rules:\n"
    "  - Do NOT approve if any Quality Bar item fails.\n"
    "  - Name the specific field that fails — not a vague concern.\n"
    "  - Do NOT call any tool other than critic_approve."
)
```

### Why this matters

The hat's Quality Bar section is already in the system message (injected by p51a). The old prompt
never told the LLM to iterate through it. The new prompt makes per-item PASS/FAIL evidence
mandatory before the final approve/reject decision.

### Verification

```bash
python3.11 -m compileall skillforge/forge.py -q
grep "Quality Bar" skillforge/forge.py | grep -v "^Binary"   # must show ≥2 hits (pre-action + critic)
grep "rubber-stamp" skillforge/forge.py | wc -l              # must be ≥ 1
grep "PASS:" skillforge/forge.py | wc -l                     # must be ≥ 1
pytest tests/test_forge_critique.py -q --tb=short -m "not live"
```

### Acceptance Criteria

- Critic prompt explicitly references "## Quality Bar section".
- Prompt requires PASS/FAIL per item before the final line.
- critic_approve is only reachable when all items pass.
- No OCI/domain vocabulary in forge.py.

---

## p51d — Phase D: Output soundness (advisory) in post-review

**File:** `skillforge/forge.py`, `_run_expert_post_review()` (lines 1344–1368)

Phase D asks: "Is this the right output for this request?" It is **advisory** — it does not change
the FINAL DECISION, so it cannot break the existing approve/iterate/surface routing. It appends
after the decision line for the main loop (and the user) to act on.

### Change 1 — Raise `_EXPERT_REVIEW_MIN_CHARS` (line 78)

```python
# BEFORE
_EXPERT_REVIEW_MIN_CHARS = 800

# AFTER
_EXPERT_REVIEW_MIN_CHARS = 1000   # four phases now require more text
```

### Change 2 — Add Phase D to `review_prompt` (after Phase C block, before FINAL DECISION)

Locate this exact block in the current `review_prompt`:
```python
"FINAL DECISION — after completing Phases A, B, and C, output EXACTLY ONE line:\n"
f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
f"  {_EXPERT_REVIEW_ITERATE}: <issue>    — at least one fixable FAIL or CONFLICT\n"
f"  {_EXPERT_REVIEW_SURFACE}: <issue>    — unfixable gap requiring user clarification\n\n"
"You MUST complete all three phases before writing the final decision line.\n"
"Do NOT call a tool here."
```

Replace with:
```python
"PHASE D — Output soundness (advisory):\n"
"Step back from the per-item checks. Is this the right output for this request?\n"
"- GOAL FIT: Does this output directly serve the stated goal? "
"Write: YES or CONCERN: <what it misses or why it is off-target>\n"
"- ANTIPATTERNS: Any structural gaps, missing constraints, or obvious failure modes "
"not covered by Phases A and B? "
"Write: NONE or FLAG: <specific issue and why it matters>\n"
"- NEXT STEP: What should the requester do or know next that they have not asked? "
"Write: NONE or SUGGEST: <specific and actionable next step>\n"
"Phase D is advisory — it does NOT change the FINAL DECISION below.\n\n"
"FINAL DECISION — after completing Phases A, B, C, and D, output EXACTLY ONE line:\n"
f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
f"  {_EXPERT_REVIEW_ITERATE}: <issue>    — at least one fixable FAIL or CONFLICT\n"
f"  {_EXPERT_REVIEW_SURFACE}: <issue>    — unfixable gap requiring user clarification\n\n"
"Then append Phase D findings as a separate block after the decision line.\n"
"Do NOT call a tool here."
```

### Why Phase D is safe

The decision routing code (lines 1413–1462) reads only the **last non-empty line** before the
phase D advisory block. As long as the FINAL DECISION line is still last before the advisory
content, the routing is unaffected. Phase D is appended after the decision.

Wait — actually the decision parsing reads `lines[-1]` where lines is all non-empty lines.
If Phase D content appears after FINAL DECISION, `lines[-1]` will be the last Phase D line,
not the decision. **Fix:** The prompt must instruct the LLM to output the decision LAST, with
Phase D BEFORE the decision line.

### Revised placement (Phase D before FINAL DECISION, decision stays last):

```python
"PHASE D — Output soundness (advisory, complete before FINAL DECISION):\n"
"Step back from the per-item checks. Is this the right output for this request?\n"
"- GOAL FIT: Does this output directly serve the stated goal? "
"Write: YES or CONCERN: <what it misses or why it is off-target>\n"
"- ANTIPATTERNS: Any structural gaps, missing constraints, or obvious failure modes "
"not covered by Phases A–C? "
"Write: NONE or FLAG: <specific issue and why it matters>\n"
"- NEXT STEP: What should the requester do or know next that they have not asked? "
"Write: NONE or SUGGEST: <specific and actionable next step>\n\n"
"FINAL DECISION — after completing all four phases, output EXACTLY ONE line last:\n"
f"  {_EXPERT_REVIEW_APPROVED}          — every Phase A + B item is PASS and Phase C is CONSISTENT\n"
f"  {_EXPERT_REVIEW_ITERATE}: <issue>    — at least one fixable FAIL or CONFLICT\n"
f"  {_EXPERT_REVIEW_SURFACE}: <issue>    — unfixable gap requiring user clarification\n\n"
"The FINAL DECISION line must be the very last line of your response.\n"
"Do NOT call a tool here."
```

Phase D findings are visible in the review_text that gets appended to the prompt — Archie sees
them when writing its final response to the user.

### Verification

```bash
python3.11 -m compileall skillforge/forge.py -q
grep "_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py   # must show 1000
grep "Phase D" skillforge/forge.py | wc -l            # must be ≥ 1
grep "ANTIPATTERNS" skillforge/forge.py | wc -l       # must be ≥ 1
grep "GOAL FIT" skillforge/forge.py | wc -l           # must be ≥ 1
# Confirm decision parsing still uses last line — no code change needed
grep "lines\[-1\]" skillforge/forge.py
pytest tests/test_forge.py tests/test_forge_critique.py -q --tb=short -m "not live"
```

### Acceptance Criteria

- Phase D appears in `review_prompt` BEFORE the FINAL DECISION block.
- FINAL DECISION line is still last — decision routing code unchanged.
- `_EXPERT_REVIEW_MIN_CHARS` is 1000.
- No OCI/domain vocabulary in forge.py.
- Phase D findings (GOAL FIT, ANTIPATTERNS, NEXT STEP) are advisory only.

---

## p52a — Surface primary risk in step3 planning

**File:** `skillforge/forge.py`, `_run_step3_planning()`, `planning_prompt` (lines 988–1011)

A one-line addition to STEP 1. Forces the planner to name the primary risk before choosing a hat
or execution plan. This is domain-agnostic — specific risk vocabulary lives in hats and Archie.

### Change — Add risk question to STEP 1 (after the ambiguity question)

```python
# Current STEP 1 block:
"STEP 1 — UNDERSTAND:\n"
"- What is the user's real goal? Name the deliverable "
"(BOM, diagram, Terraform, POV, JEP, WAF review, or none).\n"
"- Is this a new request, a revision, or a clarification?\n"
"- Is anything ambiguous? If so, what is missing?\n\n"

# After change — add one bullet:
"STEP 1 — UNDERSTAND:\n"
"- What is the user's real goal? Name the deliverable "
"(BOM, diagram, Terraform, POV, JEP, WAF review, or none).\n"
"- Is this a new request, a revision, or a clarification?\n"
"- Is anything ambiguous? If so, what is missing?\n"
"- What is the primary risk or constraint in this request? "
"(Missing required inputs, conflicting constraints, structural choices that are "
"expensive to change later, or scope that needs confirmation before work begins.) "
"Name it — do not skip.\n\n"
```

### Verification

```bash
python3.11 -m compileall skillforge/forge.py -q
grep "primary risk" skillforge/forge.py   # must appear
pytest tests/ -q --tb=short -m "not live" -k "step3 or planning" 2>&1 | tail -5
```

### Acceptance Criteria

- One additional bullet in STEP 1 — no new section, no new headers.
- `_STEP3_PLANNING_HEADERS` tuple unchanged (no new required sections to validate).
- No OCI/domain vocabulary in forge.py.

---

## p52b — Expert identity for all turns (Archie / OCI-specific)

**File:** `agent/archie_wiring.py`  
**Note:** This IS Archie code. OCI-specific content is appropriate here.

Add `_EXPERT_IDENTITY` constant and prepend it to `full_prompt` in `build_forge()`, before
`_TOOL_SEQUENCING_RULES`.

```python
_EXPERT_IDENTITY = """
## Expert Identity

You are a senior OCI Solutions Architect. Think as this expert in every interaction —
whether calling a tool, reviewing output, or answering a question in conversation.

PATTERN RECOGNITION:
Before any response, identify the architecture pattern:
3-tier web / microservices / ML inference / data platform / batch pipeline /
lift-and-shift / RAG / hybrid connectivity.
Name it. The pattern determines which OCI services are relevant and what risks to anticipate.

RISK INSTINCT:
Surface the primary risk before anything else. Do not wait for the customer to discover it.
Common OCI risks worth proactively flagging:
- No HA design for a stated production workload
- Public ingress (LB, API GW) without OCI WAF or NSG policy
- DB reachable from a public subnet
- Compartment isolation missing between prod and non-prod
- No DRG or FastConnect for stated on-prem connectivity needs
- GPU or large instance class without budget confirmation
- Terraform without explicit compartment OCID strategy

SPECIFICITY:
Never give generic cloud advice. Name the OCI service, shape, SKU, or config.
Say "VM.Standard.E5.Flex, 4 OCPU, B97384/B97385 at $0.03/OCPU-hr" — not "a compute instance."
Say "OCI WAF with OWASP Core Rule Set 3.2" — not "a web application firewall."

ASSUMPTION SURFACING:
When you default a value, name it — every time, without exception.
"Assuming us-chicago-1, single-AD, E5.Flex — confirm if your requirements differ."
Unstated assumptions are silent architecture failures.

PROACTIVE GUIDANCE:
After delivering any artifact, suggest the natural next step.
"BOM delivered. Next: generate the architecture diagram to validate topology before WAF or Terraform."
"""
```

### Where to insert in `build_forge()`

Find `full_prompt = ...` in `archie_wiring.py` and prepend `_EXPERT_IDENTITY`:
```python
full_prompt = _EXPERT_IDENTITY + "\n" + _ARCHIE_SYSTEM_PROMPT + "\n\n" + _TOOL_SEQUENCING_RULES
```
(Exact line TBD by Codex depending on current structure — search for `full_prompt` assignment.)

### Verification

```bash
python3.11 -m compileall agent/archie_wiring.py -q
grep "PATTERN RECOGNITION" agent/archie_wiring.py
grep "RISK INSTINCT" agent/archie_wiring.py
grep "PROACTIVE GUIDANCE" agent/archie_wiring.py
# Confirm Forge system prompt contains these sections at runtime:
python3.11 -c "
from agent.archie_wiring import build_forge
from agent.persistence_objectstore import InMemoryObjectStore
store = InMemoryObjectStore()
forge = build_forge(store=store, customer_id='test', customer_name='Test')
sp = forge._get_system_msg()
assert 'PATTERN RECOGNITION' in sp, 'FAIL'
assert 'RISK INSTINCT' in sp, 'FAIL'
print('PASS:', len(sp), 'chars in system prompt')
"
pytest tests/test_archie_forge_wiring.py tests/test_archie_wiring.py -q --tb=short -m "not live"
```

### Acceptance Criteria

- `_EXPERT_IDENTITY` constant defined in `agent/archie_wiring.py` (NOT in `skillforge/`).
- Prepended to full_prompt before `_TOOL_SEQUENCING_RULES`.
- OCI-specific language (E5.Flex, WAF, DRG, etc.) stays in archie_wiring.py only.
- `tests/test_archie_forge_wiring.py` passes.

---

## p52c — Populate `MemorySnapshot.formatted` for post-review memory context

**File:** `agent/archie_memory_impl.py`, `ArchieMemory.assemble()` (lines 36–62)

**The gap:** `_run_expert_post_review()` uses `memory_snapshot.formatted` for the MEMORY SNAPSHOT
block in Phase C. `MemorySnapshot.formatted` defaults to `""` and is never populated in
`ArchieMemory.assemble()`. Phase C memory consistency check currently has no data to check against.

### Change — Build `formatted` text from assembled facts

Add after the `resolved_questions` block (before the `return MemorySnapshot(...)` call):

```python
# Build prompt-ready formatted text for Phase C memory consistency check.
formatted_parts: list[str] = []
if facts_summary:
    formatted_parts.append(f"Customer facts: {facts_summary}")

if isinstance(infrastructure_profile, dict) and infrastructure_profile:
    profile_lines = [
        f"  {k}: {v}"
        for k, v in sorted(infrastructure_profile.items())
        if v not in (None, "", [], {})
    ]
    if profile_lines:
        formatted_parts.append("Infrastructure profile:\n" + "\n".join(profile_lines))

if constraints:
    constraint_lines = [
        f"  {k}: {v}"
        for k, v in sorted(constraints.items())
        if v not in (None, "", [], {})
    ]
    if constraint_lines:
        formatted_parts.append("Approved constraints:\n" + "\n".join(constraint_lines))

resolved_list = archie_state.get("resolved_questions")
if isinstance(resolved_list, list) and resolved_list:
    recent = resolved_list[-3:]   # last 3 only — avoid context bloat
    formatted_parts.append(
        "Recently resolved: " + "; ".join(str(q) for q in recent if q)
    )

formatted = "\n".join(formatted_parts)
```

And pass it to `MemorySnapshot`:
```python
return MemorySnapshot(
    session_id=session_id,
    facts=facts,
    constraints=constraints,
    prior_artifacts=_prior_artifacts(context),
    decision_context=dict(context.get("latest_decision_context") or {}),
    raw=context,
    formatted=formatted,      # ← ADD
)
```

### Why this matters

Without `formatted`, Phase C of post-review says "MEMORY SNAPSHOT (confirmed values from this
session):" followed by nothing. The expert has no ground truth to check the sub-agent result
against. With this change, Phase C can catch wrong region, wrong shape, wrong HA mode, etc.

### Verification

```bash
python3.11 -m compileall agent/archie_memory_impl.py -q
python3.11 -c "
import sys; sys.path.insert(0, '.')
from agent.archie_memory_impl import ArchieMemory
from agent.persistence_objectstore import InMemoryObjectStore

store = InMemoryObjectStore()
mem = ArchieMemory(store)

# Fake context with some facts
ctx = {
    'archie': {
        'facts_summary': 'E5.Flex 4 OCPU, us-chicago-1',
        'infrastructure_profile': {'region': 'us-chicago-1', 'shape': 'E5.Flex'},
        'latest_approved_constraints': {'ha_mode': 'single-AD'},
        'resolved_questions': ['region confirmed as us-chicago-1'],
    }
}
snap = mem.assemble(session_id='t', context=ctx, user_message='test')
assert snap.formatted, 'FAIL: formatted is empty'
assert 'E5.Flex' in snap.formatted, 'FAIL: infrastructure_profile not in formatted'
assert 'us-chicago-1' in snap.formatted
print('PASS:', repr(snap.formatted[:120]))
"
pytest tests/test_archie_memory_impl.py -q --tb=short -m "not live" 2>&1 | tail -5
```

### Acceptance Criteria

- `MemorySnapshot.formatted` is non-empty when context has facts/profile/constraints.
- Last 3 resolved questions included (not all — context budget).
- Values of `None`, `""`, `[]`, `{}` are filtered out.
- No OCI-specific text added to `skillforge/` — this change is in `agent/` only.

---

## Run Order

```
p51a  → merge first (hat_engine.py — enables Pre-Action/Post-Action in expert block)
p51b  → after p51a (forge.py — pre-action prompt)
p51c  → parallel with p51b (forge.py — critic prompt, independent block)
p51d  → parallel with p51b/c (forge.py — post-review Phase D, independent block)
p52c  → parallel with p51b/c/d (archie_memory_impl.py — independent file)
p52a  → after p51b lands (forge.py — step3 planning, touch same file)
p52b  → parallel with p52a (archie_wiring.py — independent file)
```

**Preferred PR grouping:**
- PR 1: p51a (small, unblocks everything)
- PR 2: p51b + p51c + p51d (all in forge.py — review together)
- PR 3: p52a + p52b + p52c (quality of life, independent files)

---

## What These Changes Do NOT Touch

- BOM sub-agent LLM reasoning (deterministic pipeline)
- Sub-agent implementations in `sub_agents/`
- Hat file content (hats already have strong Pre-Action/Post-Action sections)
- The decision routing logic for approve/iterate/surface (Phase D is advisory only)
- `archie_session.py` (routing, intent classification — separate concern)

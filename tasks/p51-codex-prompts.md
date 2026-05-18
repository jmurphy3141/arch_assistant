# p51/p52 Codex Prompts — Expert Reasoning Depth

## Background

Full analysis and design in `tasks/p51-expert-reasoning-depth.md`.

The enforcement machinery in SkillForge is correct (600-char minimums, required
headers, retry logic, decision sentinels). The **content** of the expert
reasoning is the gap. Six root causes:

1. Hat Pre-Action Checklist and Post-Action Review are NOT injected — the LLM
   is told to reference them but they are not in context.
2. EXPERT ASSESSMENT elicits a recommendation but not WHY — no tradeoff
   justification, no workload pattern, no top risk, no proactive flag.
3. Post-review checks technical correctness but not architectural soundness.
4. Critic pass is a 3-line rubber stamp — never applies the hat's Quality Bar.
5. Step3 planning is tool-routing only — never identifies the architectural risk.
6. No expert identity framing for conversational turns (no hat active).

Run order: p51a → p51b + p51c + p51d (parallel) → p52a + p52b + p52c (parallel).
p51a is prerequisite: once hat sections are injected, p51b/p51c operate on
actual hat content rather than LLM inference.

---

## p51a — Inject Pre-Action Checklist and Post-Action Review into expert hat block

```
Context: build_expert_block() in agent/hat_engine.py (lines 165–174) injects
hat sections into the expert system message. It injects "Core Principles",
"Quality Bar", "Output Contract", "Critic Evaluation Guidance", and
"Failure Questions" — but NOT "Pre-Action Checklist" or "Post-Action Review".

Yet _run_expert_pre_action() in forge.py tells the LLM: "List every unconfirmed
prerequisite from this hat's Pre-Action Checklist." And _run_expert_post_review()
says: "Work through each item in your hat's ## Post-Action Review section."

The LLM is told to reference sections that are not in its context. It must
hallucinate or infer from Core Principles alone. This is the largest single gap.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p51a origin/main

Read agent/hat_engine.py lines 155–185 (build_expert_block function) before
editing.

Make exactly one change to agent/hat_engine.py:

In the build_expert_block() function, find the sections tuple:

  for section in (
      "Core Principles",
      "Quality Bar",
      "Output Contract",
      "Critic Evaluation Guidance",
      "Failure Questions",
  ):

Replace with:

  for section in (
      "Core Principles",
      "Quality Bar",
      "Pre-Action Checklist",
      "Post-Action Review",
      "Output Contract",
      "Critic Evaluation Guidance",
      "Failure Questions",
  ):

Pre-Action Checklist and Post-Action Review go after Quality Bar so the LLM
sees the quality standard before the operational procedure.

Run ALL acceptance criteria:

  python3.11 -m compileall agent/hat_engine.py -q
  # must be clean

  python3.11 -c "
  from agent.hat_engine import build_expert_block
  b = build_expert_block('oci_bom_expert')
  assert 'Pre-Action Checklist' in b, 'missing Pre-Action Checklist'
  assert 'Post-Action Review' in b, 'missing Post-Action Review'
  print('PASS:', len(b), 'chars')
  "
  # must print PASS

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p51a: inject Pre-Action Checklist and Post-Action Review into expert hat block

Branch: claude/p51a (from main). Push when done.
```

---

## p51b — Expert pre-action: architectural judgment, not fact-listing

```
Context: _run_expert_pre_action() in skillforge/forge.py (lines ~1170–1196)
has an EXPERT ASSESSMENT section that asks: "As the expert, what is the right
solution? State your recommendation with specifics."

This elicits a solution but NOT architectural judgment. A senior SA doesn't
just state a recommendation — they name the workload pattern, justify their
choice over alternatives, identify the key risk, and flag what the customer
should know that they haven't asked.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p51b origin/main

Read skillforge/forge.py lines 1160–1210 (_run_expert_pre_action function)
and the _EXPERT_PRE_ACTION_HEADERS tuple before editing.

Make exactly two changes to skillforge/forge.py:

Change 1 — Replace the entire pre_action_prompt string.

Find the pre_action_prompt assignment starting with:
  pre_action_prompt = (
      f"{prompt}{retry_context}\n\n"

Replace the entire assignment (up through the closing parenthesis) with:

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

Change 2 — Update _EXPERT_PRE_ACTION_HEADERS to match the new header label.

Find _EXPERT_PRE_ACTION_HEADERS (a tuple near the top of the file or near the
function). Replace the last element:

  Old last element: "SUB-AGENT INSTRUCTIONS:"
  New last element: "SUB-AGENT TASK:"

The full tuple must become:
  _EXPERT_PRE_ACTION_HEADERS = (
      "KNOWN FACTS:",
      "GAPS:",
      "EXPERT ASSESSMENT:",
      "SUB-AGENT TASK:",
  )

Run ALL acceptance criteria:

  python3.11 -m compileall skillforge/forge.py -q
  # must be clean

  grep "WORKLOAD PATTERN" skillforge/forge.py
  # must match

  grep "WHY THIS APPROACH" skillforge/forge.py
  # must match

  grep "PROACTIVE FLAG" skillforge/forge.py
  # must match

  grep "SUB-AGENT TASK" skillforge/forge.py | wc -l
  # must be >= 2 (headers tuple + prompt body)

  grep "NEEDS_CLARIFICATION" skillforge/forge.py
  # must match (replaces old starred ★ items reference)

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p51b: expert pre-action — workload pattern, tradeoff justification, risk, proactive flag

Branch: claude/p51b (from main). Push when done.
```

---

## p51c — Structured critic pass: applies Quality Bar, no rubber-stamping

```
Context: _run_critique_pass() in skillforge/forge.py (lines ~1487–1492)
uses a 3-line critic_prompt:

  "Review the result of '{tool_name}' above.
   If the result is acceptable, call: critic_approve
   If you have concerns, describe them as plain text."

The critic hat's Quality Bar IS in the injected system message, but the prompt
never tells the LLM to apply it. An LLM receiving this prompt will take the
path of least resistance and approve. The critic is nearly always a rubber stamp.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p51c origin/main

Read skillforge/forge.py lines 1480–1510 (_run_critique_pass function) before
editing.

Make exactly one change to skillforge/forge.py:

Find the critic_prompt assignment:

  critic_prompt = (
      f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
      f"Review the result of '{tool_name}' above.\n"
      f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
      f"If you have concerns, describe them as plain text."
  )

Replace with:

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

Run ALL acceptance criteria:

  python3.11 -m compileall skillforge/forge.py -q
  # must be clean

  grep "Quality Bar section" skillforge/forge.py
  # must match

  grep "NOT rubber-stamping" skillforge/forge.py
  # must match

  grep "PASS or FAIL" skillforge/forge.py | wc -l
  # must be >= 1

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p51c: critic applies Quality Bar per item with PASS/FAIL — no rubber-stamping

Branch: claude/p51c (from main). Push when done.
```

---

## p51d — Phase D: Architectural soundness in post-review

```
Context: _run_expert_post_review() in skillforge/forge.py (lines ~1344–1381)
runs three phases:
  Phase A — Quality Bar (is it technically correct?)
  Phase B — Post-Action Review (hat checklist)
  Phase C — Memory consistency (matches what we know?)

Missing: "Is this the RIGHT architecture for this customer?"

A technically correct BOM with wrong sizing for the workload passes all three
phases. Phase D is the senior SA lens that catches this. It is advisory and
does NOT change the FINAL DECISION routing — it produces proactive flags Archie
can surface in its final response to the user.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p51d origin/main

Read skillforge/forge.py lines 1330–1400 (_run_expert_post_review function,
the review_prompt string, and _EXPERT_REVIEW_MIN_CHARS) before editing.

Make exactly two changes to skillforge/forge.py:

Change 1 — Add Phase D to the review_prompt string.

Find the PHASE C block in review_prompt. It looks like:
  "PHASE C — Memory consistency:\n"
  ...
  (text ending before FINAL DECISION)

After the Phase C block and before the FINAL DECISION block, insert:

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

Change 2 — Raise _EXPERT_REVIEW_MIN_CHARS from 800 to 1000.

Find:
  _EXPERT_REVIEW_MIN_CHARS = 800

Replace with:
  _EXPERT_REVIEW_MIN_CHARS = 1000

The post-review now has 4 phases. The higher minimum ensures Phase D gets real
content rather than being omitted to squeak past the 800-char gate.

Run ALL acceptance criteria:

  python3.11 -m compileall skillforge/forge.py -q
  # must be clean

  grep "Phase D" skillforge/forge.py
  # must match

  grep "ANTIPATTERNS" skillforge/forge.py
  # must match

  grep "NEXT STEP FLAG" skillforge/forge.py
  # must match

  grep "_EXPERT_REVIEW_MIN_CHARS" skillforge/forge.py
  # must show 1000

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p51d: Phase D architectural soundness in post-review — goal fit, antipatterns, next step

Branch: claude/p51d (from main). Push when done.
```

---

## p52a — Architectural risk in step3 planning

```
Context: _run_step3_planning() in skillforge/forge.py (lines ~998–1011)
asks three questions:
  STEP 1: What is the user's real goal?
  STEP 2: What facts are confirmed?
  STEP 3: Which hat to activate?

It routes to tools but never names the architectural risk. Archie can plan
"generate a BOM" without ever asking "what is the primary constraint or failure
mode in this workload?" A risk-aware plan prevents the expert pre-action from
being surprised.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p52a origin/main

Read skillforge/forge.py lines 990–1020 (_run_step3_planning function and
_STEP3_PLANNING_HEADERS tuple) before editing.

Make exactly one change to skillforge/forge.py:

In the planning_prompt string, find the STEP 1 block. It will contain lines like:
  "STEP 1 — UNDERSTAND:\n"
  "- What is the user's real goal?..."
  "- Is this a new request, a revision, or a clarification?\n"
  "- Is anything ambiguous?...\n"

After the last sub-bullet of STEP 1 (before STEP 2 begins), add:
  "- What is the primary architectural risk or constraint in this request? "
  "(HA exposure, compliance requirement, budget ceiling, migration complexity, "
  "public ingress without filtering, data sensitivity). Name it — do not skip.\n"

This is an additional sub-question within STEP 1. Do NOT add a new required
header to _STEP3_PLANNING_HEADERS — the validation tuple is unchanged.

Run ALL acceptance criteria:

  python3.11 -m compileall skillforge/forge.py -q
  # must be clean

  grep "primary architectural risk" skillforge/forge.py
  # must match

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p52a: step3 planning surfaces primary architectural risk before tool selection

Branch: claude/p52a (from main). Push when done.
```

---

## p52b — Expert identity for all turns

```
Context: When no hat is active (architecture discussion, question answering,
follow-up turns), Archie has no OCI-specific expert framing in its system
prompt beyond generic "be architect-level" instructions. An SA asking "what's
the best HA approach for a 3-tier web app?" gets a helpful assistant response,
not an expert architect response that names specific OCI topology, shapes,
and risks.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p52b origin/main

Read agent/archie_wiring.py lines 24–66 (_TOOL_SEQUENCING_RULES and build_forge)
before editing.

Make exactly two changes to agent/archie_wiring.py:

Change 1 — Add _EXPERT_IDENTITY constant after the imports block and before
_TOOL_SEQUENCING_RULES. Insert:

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

Change 2 — In build_forge(), find the full_prompt assembly:

  full_prompt = (
      routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
  ).strip()

Replace with:

  full_prompt = (
      _EXPERT_IDENTITY + "\n\n" + routing_guidance + "\n\n" + base_system_prompt + "\n\n" + _TOOL_SEQUENCING_RULES
  ).strip()

_EXPERT_IDENTITY goes first so it establishes persona before tool routing rules.

Run ALL acceptance criteria:

  python3.11 -m compileall agent/archie_wiring.py -q
  # must be clean

  grep "PATTERN RECOGNITION" agent/archie_wiring.py
  # must match

  grep "RISK INSTINCT" agent/archie_wiring.py
  # must match

  grep "PROACTIVE GUIDANCE" agent/archie_wiring.py
  # must match

  grep "_EXPERT_IDENTITY" agent/archie_wiring.py | wc -l
  # must be >= 2 (definition + reference in full_prompt)

  pytest tests/test_archie_forge_wiring.py -v --tb=short
  # all tests must pass

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p52b: expert identity — pattern recognition, risk instinct, specificity, proactive guidance

Branch: claude/p52b (from main). Push when done.
```

---

## p52c — Enrich memory injection

```
Context: ArchiePromptEnricher in agent/archie_memory_impl.py (lines 80–97)
injects only facts_summary and constraints into the prompt before each LLM call.
MemorySnapshot contains additional fields that are populated in some sessions
but never surfaced to the expert:
  - infrastructure_profile: confirmed infrastructure details
  - resolved_questions: previously answered architecture questions

Expert KNOWN FACTS reasoning in pre-action currently operates on a compressed
partial view of session knowledge. When these fields have content, the expert
should see them.

IMPORTANT: Branch from origin/main.

  git fetch origin
  git checkout -b claude/p52c origin/main

Read agent/archie_memory_impl.py lines 78–100 (ArchiePromptEnricher.__call__)
before editing.

Make exactly one change to agent/archie_memory_impl.py:

In ArchiePromptEnricher.__call__(), after the constraints injection block
(the if memory.constraints: block) and before the return statement, add:

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

Run ALL acceptance criteria:

  python3.11 -m compileall agent/archie_memory_impl.py -q
  # must be clean

  grep "infrastructure_profile" agent/archie_memory_impl.py
  # must match

  grep "resolved_questions" agent/archie_memory_impl.py
  # must match

  python3.11 -c "
  from agent.archie_memory_impl import ArchiePromptEnricher
  from skillforge.types import MemorySnapshot
  e = ArchiePromptEnricher()
  snap = MemorySnapshot(facts={'infrastructure_profile': 'VCN in us-chicago-1', 'resolved_questions': 'HA: single-AD confirmed'}, constraints={})
  result = e('test prompt', snap)
  assert 'Archie Infrastructure Profile' in result
  assert 'Archie Resolved Questions' in result
  print('PASS')
  "
  # must print PASS

  pytest tests/ -q --tb=short -m "not live" -x 2>&1 | tail -5
  # no new failures vs baseline

Commit message:
p52c: enrich memory injection — infrastructure profile and resolved questions

Branch: claude/p52c (from main). Push when done.
```

---

## Summary

| Task | File | Change | Priority |
|---|---|---|---|
| p51a | `agent/hat_engine.py` | Add 2 missing sections to `build_expert_block()` | CRITICAL |
| p51b | `skillforge/forge.py` | Rebuild pre-action prompt with WORKLOAD PATTERN, WHY, TOP RISK, PROACTIVE FLAG | HIGH |
| p51c | `skillforge/forge.py` | Replace 3-line critic stub with structured Quality Bar pass/fail review | HIGH |
| p51d | `skillforge/forge.py` | Add Phase D (architectural soundness) to post-review; raise min to 1000 | HIGH |
| p52a | `skillforge/forge.py` | Add architectural risk question to step3 STEP 1 | MEDIUM |
| p52b | `agent/archie_wiring.py` | Add `_EXPERT_IDENTITY` constant; prepend to system prompt | MEDIUM |
| p52c | `agent/archie_memory_impl.py` | Inject `infrastructure_profile` and `resolved_questions` | MEDIUM |

Run order: p51a first (prerequisite for p51b/p51c/p51d to have full effect).
p51b, p51c, p51d are independent code changes and may be branched in parallel.
p52a, p52b, p52c are independent and may be branched in parallel after p51a merges.

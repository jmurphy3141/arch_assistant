# Task p39a: Manager Reasoning Loop Skill File

## Goal

Create `skills/manager_reasoning_loop.md` — a global skill file that is
registered with Forge via `register_skill_file()` and injected into every
turn's system prompt. The skill gives the LLM explicit, ordered instructions
for reasoning through all 6 steps before acting.

---

## Scope

**Only touch:** `skills/manager_reasoning_loop.md` (new file).  
**Do NOT touch:** Python files, tests, hat files, or other skills.

---

## Prerequisite Check

```bash
ls skills/
cat skills/intent_routing.md | head -5   # confirm existing skill format
```

---

## What to implement

Create `skills/manager_reasoning_loop.md` with exactly the content below.

```markdown
# Manager Reasoning Loop

Every turn, reason through the six steps below **before** calling any tool.
You do not need to surface all six steps to the user — they are your internal
reasoning scaffold. Only the final reply or tool call is shown.

---

## Step 1 — Understand the Request

Before anything else, identify:
- What is the user's real goal? (not just what they typed)
- Is this a clarification, a revision, or a new request?
- Is there an implicit deliverable (BOM, diagram, Terraform, POV, JEP, WAF)?
- If the request is ambiguous, note exactly what is missing.

Do NOT proceed to tool dispatch until you have named the goal.

---

## Step 2 — Assess Memory & Context

Review available context before deciding anything:
- What has already been decided (shapes, region, services, budget, HA mode)?
- What is explicitly unknown or unconfirmed?
- Is the current memory sufficient to produce a complete deliverable, or are
  there gaps that require clarification?

If critical gaps exist and the user has not provided the information, use
Step 3 to plan a clarification response — do not call a generation tool.

---

## Step 3 — Plan & Select Hat

Decide your approach:
- Which deliverable is needed? Which expert hat (if any) should be worn?
- Is there enough information to proceed, or should you ask one focused
  clarifying question?
- Which tool (or sequence of tools) achieves the goal?
- If a hat is needed and not yet active, activate it now before proceeding.

Hat activation rules:
- `use_hat_oci_bom_expert` — cost, pricing, BOM, XLSX, SKUs, sizing
- `use_hat_diagram_for_oci` — architecture diagram, draw.io, topology
- `use_hat_terraform_for_oci` — HCL generation, Terraform modules, OCI provider
- `use_hat_oci_waf_reviewer` — WAF review, security assessment, compliance
- `use_hat_oci_customer_pov_writer` — POV document, competitive narrative
- `use_hat_jep_writer` — JEP, POC plan, project execution plan
- Do NOT activate critic or governor manually — they fire automatically.

---

## Step 4 — Expert Pre-Action (when a hat is active)

Before calling any sub-agent or generation tool, verify the hat's
`## Pre-Action Checklist`. The checklist is in the `[ACTIVE EXPERT]` block
of the current system prompt. Confirm every prerequisite is met:
- If all prerequisites are satisfied → proceed to Step 5.
- If any prerequisite is missing → ask the user the specific question from
  the hat's `## Failure Questions` section.

Do NOT call a generation tool if a prerequisite is unmet. Surface the gap
as a focused question instead.

---

## Step 5 — Execute

Call the appropriate tool with complete, accurate arguments:
- Pass all known context (region, sizing, shapes, budget, HA mode) in the args.
- Do not omit context that was established in earlier turns.
- Do not fabricate values — use only what is confirmed in memory.

---

## Step 6 — Post-Action Review (when a hat is active)

After the tool returns, review the result **while still wearing the hat**
before the critic fires. Check against the hat's `## Post-Action Review`
section in the `[ACTIVE EXPERT]` block:
- Does the result satisfy all quality bar items?
- Are there obvious gaps, wrong values, or missing fields?
- If the result is incomplete or incorrect, note the specific issue.

The critic hat fires automatically after your expert self-review. Your
expert review is the first filter; the critic is the second.

---

## Loop Contract

- Steps 1–3 happen once per turn, before the first tool call.
- Steps 4–6 happen once per tool call when a hat is active.
- A plain conversational reply (no tool) skips Steps 4–6 entirely.
- Never call more than one generation tool per iteration unless the tool
  returns `status: parallel` — let the loop handle chaining.
```

---

## Acceptance Criteria

1. `skills/manager_reasoning_loop.md` exists.
2. File contains all 6 step headings:
   ```bash
   grep "Step 1\|Step 2\|Step 3\|Step 4\|Step 5\|Step 6" skills/manager_reasoning_loop.md | wc -l
   ```
   Must return ≥ 6.
3. File references `Pre-Action Checklist` and `Post-Action Review`:
   ```bash
   grep "Pre-Action Checklist\|Post-Action Review" skills/manager_reasoning_loop.md
   ```
4. `python3.11 -m compileall skillforge/forge.py` — exits 0 (no Python changes
   in this task; verify forge.py is unmodified).
5. `pytest tests/test_forge.py -q --tb=short` — same pass count as before.

---

## Commit Message

```
p39a: create skills/manager_reasoning_loop.md — 6-step reasoning scaffold
```

Branch: `claude/p39a` (from main). Push when done.

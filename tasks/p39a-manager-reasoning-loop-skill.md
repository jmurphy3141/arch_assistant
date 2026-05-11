# Task p39a: Define and Implement the Manager Reasoning Loop

## Objective

Make the manager (Archie / Forge) follow a clear 6-step reasoning loop that
uses hats for deep expert thinking. The manager wears the hat — it thinks as
the expert before calling any sub-agent and reviews as the expert afterward.

**Critical distinction:** The hat is worn by the manager LLM, not by the
sub-agent. Sub-agents are execution engines. The manager is the expert.

---

## Scope

**Touch:**
- `skillforge/forge.py` — add `_run_expert_pre_action()` method + wiring
- `skills/manager_reasoning_loop.md` — new global skill file

**Do NOT touch:** hat files, tests, other Python modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py
ls skills/
grep "_run_expert_pre_action\|planning_call" skillforge/forge.py  # should be zero
```

---

## Part 1 — Create `skills/manager_reasoning_loop.md`

Create this file with exactly the following content:

```markdown
# Manager Reasoning Loop

You are the manager (Archie). When a hat is active, YOU wear the hat — you
think as that expert. Sub-agents execute; you reason. Every turn follows the
six steps below.

---

## Step 1 — Understand the Request

Name the user's real goal before doing anything else:
- What deliverable is actually being requested (BOM, diagram, Terraform, POV,
  JEP, WAF review — or none)?
- Is this a new request, a revision, or a clarification?
- Is the request ambiguous? If so, identify exactly what is missing.

Do not proceed to Step 2 until you have named the goal.

---

## Step 2 — Memory & Context Assessment

Review what is known before deciding anything:
- What facts are already confirmed (shapes, region, services, budget, HA mode,
  customer name, compliance scope)?
- What is missing or unconfirmed?
- Is there enough information to produce a complete deliverable?

If critical information is missing, your Step 3 plan is to ask — not to
generate. Do not call a sub-agent when prerequisites are unmet.

---

## Step 3 — Planning & Hat Selection

Choose your approach:
- Which hat (if any) should you activate? Activate it now, before Step 4.
- Is there enough context to proceed to execution, or do you need to clarify?
- What will you tell the sub-agent? (You decide the instructions as the expert.)

Hat selection guide:
- `use_hat_oci_bom_expert` → cost, pricing, BOM, XLSX, SKU, sizing
- `use_hat_diagram_for_oci` → architecture diagram, draw.io, OCI topology
- `use_hat_terraform_for_oci` → Terraform HCL, OCI provider, modules
- `use_hat_oci_waf_reviewer` → WAF, security, compliance assessment
- `use_hat_oci_customer_pov_writer` → POV document, competitive narrative
- `use_hat_jep_writer` → JEP, POC plan, phased execution plan
- Critic and governor activate automatically — never activate them manually.

---

## Step 4 — Expert Pre-Action Thinking (mandatory when hat is active)

Before calling any sub-agent or tool, YOU think as the expert:

**Known facts:** What has the user confirmed? What have we agreed on in prior
turns? State the specific values (e.g., "E4.Flex, 8 OCPU, us-chicago-1,
active-active HA, 500 GB Block Volume, no BYOL").

**Gaps:** What prerequisite from this hat's Pre-Action Checklist is still
missing? If any gap exists, do not call the sub-agent — ask the user first.

**Approach:** As the expert, what is the right solution? What shape family,
what topology, what modules, what findings — before the sub-agent runs?

**Instructions:** What precise task will you give the sub-agent? The sub-agent
should receive expert-level instructions, not a raw user message.

This step produces internal reasoning. Log it. Use it to craft better tool args.

---

## Step 5 — Execution

Call the tool with expert-crafted arguments:
- Include all confirmed context — do not omit facts established in prior turns.
- Use the reasoning from Step 4 to fill in the tool's task/prompt argument.
- Do not fabricate values — only use confirmed or defaulted-with-justification facts.

---

## Step 6 — Post-Action Review (mandatory when hat is active)

After the sub-agent returns, YOU review the result as the expert:

Check the hat's Post-Action Review checklist (in the [ACTIVE EXPERT] block).
For each item:
- Pass → continue
- Fail → note the specific field and expected value

Decision after review:
- All checks pass → approve for critic
- Fixable gap → iterate: call the sub-agent again with a correction
- Unfixable gap → surface the issue to the user with a clear explanation

Only after your expert review passes does the critic hat fire.
```

---

## Part 2 — Add `_run_expert_pre_action()` to `skillforge/forge.py`

### 2a. Add the method

Add this private method to the `Forge` class, near `_run_critique_pass()`
(around line 725 in the current file):

```python
async def _run_expert_pre_action(
    self,
    *,
    prompt: str,
    tool_name: str,
    tool_args: dict,
    active_hats: list[str],
    session_id: str,
) -> str:
    """
    Step 4 of the manager reasoning loop: expert pre-action thinking.

    The manager thinks as the active expert before calling a sub-agent.
    Covers: known facts, gaps, approach, and instructions for the sub-agent.
    Output is appended to prompt as EXPERT_THINKING and logged at INFO.
    No-op when no expert hat is active.
    """
    expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
    if not expert_hats:
        return prompt

    hat_label = ", ".join(expert_hats)
    pre_action_prompt = (
        f"{prompt}\n\n[STEP 4 — EXPERT PRE-ACTION THINKING]\n"
        f"You are wearing the {hat_label} hat. Before calling '{tool_name}', "
        "think deeply as the expert. Cover:\n"
        "1. Known facts: what has been confirmed in this session?\n"
        "2. Gaps: does this hat's Pre-Action Checklist have any unmet items?\n"
        "3. Approach: as the expert, what is the right solution?\n"
        "4. Instructions: what precise task will you give the sub-agent?\n"
        "Output your reasoning as plain text. Do NOT call a tool here."
    )
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(pre_action_prompt, system_msg, "expert_pre_action")
    except Exception:
        logger.exception(
            "Expert pre-action call failed session=%s tool=%s", session_id, tool_name
        )
        return prompt

    reasoning = raw.strip()
    if reasoning:
        logger.info(
            "Expert pre-action [%s] for tool '%s' session=%s:\n%s",
            hat_label, tool_name, session_id, reasoning,
        )
        prompt = f"{prompt}\n\nEXPERT_THINKING:\n{reasoning}"
    return prompt
```

### 2b. Wire `_run_expert_pre_action()` into `run_turn()`

In `run_turn()`, locate the domain tool dispatch section. The existing code
(around the `# ── Domain tool ───` comment) looks like:

```python
            # ── Domain tool ───────────────────────────────────────────────────

            # Inject skill_guidance into the task/prompt arg before dispatch.
            if spec.skill_guidance:
                ...

            mem = memory_snapshot if spec.memory_contract else None
            try:
                result = await spec.handler(...)
```

Insert the pre-action call **after** skill_guidance injection and **before**
the `spec.handler(...)` call. The pre-action only fires for critique-enabled
tools (the tools that warrant expert oversight):

```python
            # Step 4: expert pre-action thinking (fires for critique-enabled tools)
            if spec.critique_enabled:
                prompt = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                )
```

Add this block immediately before `mem = memory_snapshot if spec.memory_contract else None`.

---

## Acceptance Criteria

1. `skills/manager_reasoning_loop.md` exists and contains all 6 step headings:
   ```bash
   grep "Step 1\|Step 2\|Step 3\|Step 4\|Step 5\|Step 6" skills/manager_reasoning_loop.md | wc -l
   # must be ≥ 6
   ```

2. File makes the manager/hat ownership explicit:
   ```bash
   grep "YOU wear the hat\|you think as\|Sub-agents execute" skills/manager_reasoning_loop.md
   # must match
   ```

3. `_run_expert_pre_action` is present in forge.py:
   ```bash
   grep "_run_expert_pre_action" skillforge/forge.py | wc -l
   # must be ≥ 2 (definition + call site)
   ```

4. Expert thinking is logged at INFO level:
   ```bash
   grep "logger.info.*expert_pre_action\|logger.info.*EXPERT\|Expert pre-action" skillforge/forge.py
   # must match
   ```

5. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py
   ```

6. No regressions:
   ```bash
   pytest tests/test_forge.py -q --tb=short
   ```

---

## Commit Message

```
p39a: 6-step manager reasoning loop — skill file + expert pre-action thinking (Step 4)
```

Branch: `claude/p39a` (from main). Push when done.

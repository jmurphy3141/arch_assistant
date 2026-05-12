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

**KNOWN FACTS:** What has the user confirmed? What have we agreed on in prior
turns? State every specific value (e.g., "E4.Flex, 8 OCPU, us-chicago-1,
active-active HA, 500 GB Block Volume, no BYOL"). Do not use vague summaries.

**GAPS:** What prerequisite from this hat's Pre-Action Checklist is still
missing? If any starred (★) required item is missing, do not call the
sub-agent. Ask the user first with `NEEDS_CLARIFICATION: <question>`.

**EXPERT ASSESSMENT:** As the expert, what is the right solution? What shape
family, what topology, what modules, what findings — before the sub-agent runs?

**SUB-AGENT INSTRUCTIONS:** What precise task will you give the sub-agent? The
sub-agent should receive expert-level instructions, not a raw user message.

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
_EXPERT_THINKING_MIN_CHARS = 300

async def _run_expert_pre_action(
    self,
    *,
    prompt: str,
    tool_name: str,
    tool_args: dict,
    active_hats: list[str],
    session_id: str,
    events: list,
) -> tuple[str, str | None]:
    """
    Step 4 of the manager reasoning loop: expert pre-action thinking.

    The manager thinks as the active expert before calling a sub-agent.
    Uses a structured 4-section format to force depth: KNOWN FACTS, GAPS,
    EXPERT ASSESSMENT, SUB-AGENT INSTRUCTIONS.

    Returns (updated_prompt, clarification_needed).
    clarification_needed is None when the expert is ready to proceed.
    clarification_needed is a question string when a starred prerequisite is unmet.
    No-op (returns (prompt, None)) when no expert hat is active.
    """
    expert_hats = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
    if not expert_hats:
        return prompt, None

    hat_label = ", ".join(expert_hats)
    pre_action_prompt = (
        f"{prompt}\n\n"
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
    system_msg = self._build_active_system_msg(active_hats)

    try:
        raw = await self._text_runner(pre_action_prompt, system_msg, "expert_pre_action")
    except Exception:
        logger.exception(
            "[EXPERT_PRE_ACTION] Call failed session=%s tool=%s", session_id, tool_name
        )
        return prompt, None

    reasoning = raw.strip()

    # Shallow-response guard: retry once if response is too brief.
    if (
        len(reasoning) < _EXPERT_THINKING_MIN_CHARS
        and not reasoning.startswith("NEEDS_CLARIFICATION:")
    ):
        logger.warning(
            "[EXPERT_PRE_ACTION] Shallow response (%d chars) for tool '%s' session=%s — retrying",
            len(reasoning), tool_name, session_id,
        )
        retry_prompt = (
            f"{pre_action_prompt}\n\n"
            "[Your previous response was too brief. A senior expert would write at least "
            "3 specific bullet points per section. Retry with full depth — be specific "
            "about values, part numbers, topologies, or module names as appropriate.]"
        )
        try:
            raw = await self._text_runner(
                retry_prompt, system_msg, "expert_pre_action_retry"
            )
            reasoning = raw.strip()
        except Exception:
            logger.exception(
                "[EXPERT_PRE_ACTION] Retry failed session=%s tool=%s",
                session_id,
                tool_name,
            )
        if len(reasoning) < _EXPERT_THINKING_MIN_CHARS:
            logger.warning(
                "[EXPERT_PRE_ACTION] Still shallow after retry (%d chars) session=%s tool=%s",
                len(reasoning), session_id, tool_name,
            )

    if reasoning.startswith("NEEDS_CLARIFICATION:"):
        clarification = reasoning[len("NEEDS_CLARIFICATION:"):].strip()
        logger.info(
            "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s → NEEDS_CLARIFICATION: %s",
            hat_label, tool_name, session_id, clarification,
        )
        events.append(
            TurnEvent(
                type="expert_pre_action",
                message=f"Expert pre-action [{hat_label}]: clarification needed",
                data={"hat": hat_label, "tool": tool_name, "clarification": clarification},
            )
        )
        return prompt, clarification

    if reasoning:
        logger.info(
            "[EXPERT_PRE_ACTION] [%s] tool='%s' session=%s:\n%s",
            hat_label, tool_name, session_id, reasoning,
        )
        events.append(
            TurnEvent(
                type="expert_pre_action",
                message=f"Expert pre-action [{hat_label}] for '{tool_name}'",
                data={"hat": hat_label, "tool": tool_name, "reasoning": reasoning},
            )
        )
        prompt = f"{prompt}\n\nEXPERT_THINKING:\n{reasoning}"
    return prompt, None
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
the `spec.handler(...)` call. The pre-action fires for all domain tools when
any expert hat is active; `critique_enabled` only gates post-review and critic.

```python
            # Step 4: expert pre-action thinking (fires for any domain tool when hat active)
            expert_hats_active = [h for h in active_hats if h not in _MANUAL_ONLY_HATS]
            if expert_hats_active:
                prompt, clarification_needed = await self._run_expert_pre_action(
                    prompt=prompt,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    active_hats=active_hats,
                    session_id=session_id,
                    events=events,
                )
                if clarification_needed:
                    reply = clarification_needed
                    break
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

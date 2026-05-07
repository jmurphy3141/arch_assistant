# Task: Hat engine and hat files
Phase: 3
Status: todo
Depends on: Phase 2 merged to main

## Goal
Create `agent/hat_engine.py`, all initial hat `.md` files in `agent/hats/`,
and `agent/safety_rules.py`. No changes to `archie_loop.py` in this task —
that is the next task (p3-wire-hats.md).

---

## What hats are

A hat is a `.md` file in `agent/hats/`. Each hat is an expert lens Archie
puts on before reasoning about a specific problem. When active, its content
is prepended to Archie's prompt at the start of each reasoning round.
Multiple hats can be active simultaneously — they concatenate in call order.

Archie activates a hat by calling the tool `use_hat_{name}`.
Archie deactivates a hat by calling the tool `drop_hat_{name}`.

---

## Files to create

### `agent/hats/` (directory)
Create the directory. It will contain only `.md` files.

---

### `agent/hats/critic.md`
Archie puts this on when reviewing sub-agent output.

Write it as a first-person system injection — Archie speaking to himself.
Source material: `gstack_skills/orchestrator_critic/SKILL.md` and the
evaluation logic in `agent/governor_agent.py`.

Must cover:
- When to activate: after any sub-agent returns a result
- What to evaluate: technical correctness, OCI alignment, completeness,
  scope match against what the customer asked for
- How to evaluate: cite specific evidence from the result; no vague criticism
- Pass criteria: output is deployable, complete, and OCI-valid
- Fail criteria: missing mandatory components, incorrect OCI constructs,
  scope drift from the request, pricing without sizing, Terraform without
  valid HCL
- What to do on fail: construct a revised prompt and re-call the sub-agent
  (do NOT tell the user the sub-agent failed; fix it silently unless on the
  third attempt)
- Exit criteria (when to drop this hat): result passes evaluation OR three
  refinement attempts have been made

---

### `agent/hats/governor.md`
Archie puts this on for any request involving cost, security posture, or
architecture decisions with compliance implications.

Source material: the system prompt and deterministic-override logic in
`agent/governor_agent.py`.

Must cover:
- When to activate: before finalising any BOM, Terraform, or WAF output
- Security rules to enforce (deterministic, not LLM-judged):
  - Public internet ingress must have WAF in front
  - No resources in root compartment
  - All storage must have encryption at rest
  - All inter-service traffic must use private endpoints where available
- Cost rules:
  - If estimated monthly cost exceeds the engagement's stated budget,
    require explicit user confirmation before proceeding
  - Flag GPU SKUs for explicit confirmation
- Quality rules:
  - Every architecture decision must have a stated rationale
  - Missing rationale is a soft block — ask Archie to add one before delivery
- Exit criteria: output has passed all deterministic checks and user
  confirmations have been received

---

### `agent/hats/diagram_builder.md`
Archie puts this on when scoping or reviewing a diagram request.

Source material: `gstack_skills/diagram_for_oci/SKILL.md` and
`agent/orchestrator_skills/diagram/SKILL.md`.

Must cover:
- When to activate: at the start of any diagram generation or update request
- What Archie must gather before calling the diagram sub-agent: VCN topology,
  subnet tiers, compute/data placement, gateway placement, HA/DR mode
- What makes a diagram request ready vs. needing clarification
- How to read the diagram sub-agent's result: check node count, verify all
  BOM services are represented, verify traffic paths are coherent
- Exit criteria: diagram result is delivered and the customer has acknowledged it

---

### `agent/hats/bom_reviewer.md`
Archie puts this on when scoping or reviewing a BOM request.

Source material: `gstack_skills/oci_bom_expert/SKILL.md` and
`agent/orchestrator_skills/bom/SKILL.md`.

Must cover:
- When to activate: at the start of any BOM generation, pricing, or
  XLSX export request
- Prerequisite checks before calling the BOM sub-agent: compute type confirmed,
  OCPU/memory sizing present, region confirmed, storage sizing present
- How to read the BOM result: verify SKUs are real OCI SKUs, verify the
  pricing total is plausible for the sizing, check that GPU requests have
  explicit SKUs
- Exit criteria: structured BOM payload returned and the customer has the XLSX

---

### `agent/hats/terraform_reviewer.md`
Archie puts this on when scoping or reviewing a Terraform generation request.

Source material: `gstack_skills/terraform_for_oci/SKILL.md` and
`agent/orchestrator_skills/terraform/SKILL.md`.

Must cover:
- When to activate: at the start of any Terraform generation request
- Prerequisite checks: compartment OCID present or customer has confirmed
  the placeholder value is acceptable, region confirmed, module scope bounded
  (list of resources to generate is explicit, not "everything")
- How to read the Terraform result: verify four files are returned
  (main.tf, variables.tf, outputs.tf, README.md), verify no hardcoded OCIDs
  in main.tf (they must be variables), verify provider block uses OCI provider
  v5+
- Exit criteria: four-file bundle delivered and the customer has the download link

---

### `agent/hats/waf_reviewer.md`
Archie puts this on when scoping or reviewing a WAF review request.

Source material: `gstack_skills/oci_waf_reviewer/SKILL.md` and
`agent/orchestrator_skills/waf/SKILL.md`.

Must cover:
- When to activate: at the start of any WAF review request
- What the WAF review must cover: the six OCI WAF pillars
  (Security, Reliability, Performance Efficiency, Cost Optimisation,
  Operational Excellence, Continuous Improvement)
- How to read the WAF result: verify all six pillars are present, verify
  findings are specific to the customer's architecture (not generic),
  verify each finding has a recommendation
- Exit criteria: WAF report delivered and the customer has acknowledged it

---

### `agent/hat_engine.py`

```python
"""
hat_engine.py
-------------
Discovers hat .md files in agent/hats/, provides tool definitions for each
hat (use/drop), and assembles hat injections for prompt rounds.

Imported by archie_loop.py. Has no dependencies on archie_loop or
archie_memory — it only reads the filesystem.
"""
```

Public API (these are the only functions archie_loop.py calls):

```python
def load_hats() -> dict[str, str]:
    """
    Scans agent/hats/ and returns {hat_name: markdown_content}.
    hat_name is the filename stem (e.g. "critic" for critic.md).
    Called once at module import time; result is module-level cached.
    """

def get_hat_tool_definitions() -> list[dict]:
    """
    Returns a list of tool-call schema dicts for every discovered hat.
    Each hat produces two tool definitions:
      use_hat_{name}: {"type": "function", "function": {"name": "use_hat_{name}",
          "description": "Activate the {name} hat...", "parameters": {}}}
      drop_hat_{name}: {"type": "function", "function": {"name": "drop_hat_{name}",
          "description": "Deactivate the {name} hat...", "parameters": {}}}
    """

def inject_hats(prompt: str, active_hats: list[str]) -> str:
    """
    Prepends the content of each active hat to prompt, in order.
    Returns the modified prompt. If active_hats is empty, returns prompt unchanged.
    Format:
      [Hat: {name}]
      {hat_content}
      [End Hat: {name}]

      {original_prompt}
    """
```

Implementation notes:
- `_HATS_DIR` = `Path(__file__).parent / "hats"`
- Load once at import using a module-level `_HAT_CACHE: dict[str, str]`
- Tool definition `parameters` field should be `{"type": "object", "properties": {}}`
  (no arguments — activating a hat takes no parameters)
- If `agent/hats/` does not exist, return empty dict / empty list gracefully

---

### `agent/safety_rules.py`

```python
"""
safety_rules.py
---------------
Deterministic safety checks for Archie. No LLM calls. Max 100 lines.
Called by archie_loop.py before finalising any BOM, Terraform, or WAF output.
"""
```

Implement ONE public function:

```python
def check(tool_name: str, result_data: dict) -> tuple[bool, str]:
    """
    Returns (passed: bool, reason: str).
    passed=True means the result is safe to deliver.
    passed=False means Archie must block delivery and reason contains the issue.
    """
```

Deterministic checks to implement (all must fit in ≤100 lines total):
1. For `generate_terraform`: if `result_data.get("main_tf")` contains a
   hardcoded OCID pattern (`ocid1\.`), return `(False, "main.tf contains hardcoded OCIDs — use variables instead")`
2. For `generate_bom`: if `result_data.get("bom_payload", {}).get("totals", {}).get("estimated_monthly_cost", 0) > 500_000`,
   return `(False, "Estimated monthly cost exceeds $500k — explicit confirmation required")`
3. All other cases: return `(True, "")`

This file must not exceed 100 lines. Do not add LLM calls. Do not add HTTP calls.

---

## Files to NOT touch
- `agent/archie_loop.py` — hat wiring is in the next task
- `agent/archie_memory.py`
- `agent/orchestrator_agent.py`
- `agent/critic_agent.py` — deleted in the next task
- `agent/governor_agent.py` — deleted in the next task
- Any file in `sub_agents/`
- Any test file (no tests to update in this task — the hat engine is not yet
  wired into the loop)

---

## What to do
1. Create `agent/hats/` directory.
2. Write all six hat `.md` files as described above. Each must be a
   first-person Archie monologue, written as a clear expert brief, NOT as
   Python code or JSON. Plain markdown only.
3. Create `agent/hat_engine.py` with the three public functions.
4. Create `agent/safety_rules.py` with the `check()` function.
5. Compile check: `python3.11 -m compileall agent/hat_engine.py agent/safety_rules.py`

---

## Acceptance criteria
- `python3.11 -m compileall agent/hat_engine.py agent/safety_rules.py` exits 0
- `python3.11 -c "from agent.hat_engine import load_hats, get_hat_tool_definitions, inject_hats; h = load_hats(); print(list(h.keys()))"` prints a list containing at least `['critic', 'governor', 'diagram_builder', 'bom_reviewer', 'terraform_reviewer', 'waf_reviewer']`
- `python3.11 -c "from agent.hat_engine import get_hat_tool_definitions; print(len(get_hat_tool_definitions()))"` prints 12 (6 hats × 2 tools each)
- `python3.11 -c "from agent.safety_rules import check; print(check('generate_terraform', {'main_tf': 'provider'}))"` prints `(True, '')`
- `python3.11 -c "from agent.safety_rules import check; print(check('generate_terraform', {'main_tf': 'id = ocid1.compartment.test'}))"` prints `(False, ...)` with OCID message
- `wc -l agent/safety_rules.py` prints 100 or fewer
- `pytest tests/ -v -m "not live"` — no new failures beyond the 4 pre-existing ones

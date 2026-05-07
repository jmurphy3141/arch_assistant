# Task p35m: Declarative Hat Transition & Coordination Rules

## Goal

Skill files can declare `coordination` rules covering triggers, parallel hat
execution, handoff messages, and synthesis steps. `Forge` reads these rules
before each turn and can automatically suggest or execute transitions.

Both `p35k` and `p35l` must be merged before this task.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
grep "get_transition_suggestions" agent/hat_engine.py   # p35k present
grep "build_memory_view_block" agent/hat_engine.py      # p35l present
pytest tests/ -q --tb=short 2>&1 | tail -5
```

---

## Scope

**Modify:**
- `agent/hats/*.md` — add `coordination` sections to all 6 files
- `agent/hat_engine.py` — add `get_coordination_rules()`
- `skillforge/forge.py` — coordination rule evaluation, parallel hint status events,
  handoff messages, synthesis step log

**Do NOT touch:** `archie_wiring.py`, handler files, memory modules.

---

## 1. `coordination` YAML Section

Add the following `coordination` block to each hat file's frontmatter.
Replace the current `coordination: {}` placeholder.

### `bom_reviewer.md`

```yaml
coordination:
  triggers:
    - "user mentions cost, budget, pricing, or SKU"
    - "BOM generation is complete"
  recommended_hats:
    - "diagram_builder"
  parallel_with: []
  handoff_message: "BOM review complete. Suggesting diagram generation next."
  synthesis_step: null
  required_approvals: []
```

### `diagram_builder.md`

```yaml
coordination:
  triggers:
    - "user requests a diagram or topology"
    - "BOM is approved and diagram is next step"
  recommended_hats:
    - "waf_reviewer"
  parallel_with:
    - "terraform_reviewer"
  handoff_message: "Diagram generation complete. WAF review and Terraform can run next."
  synthesis_step: "after waf_reviewer and terraform_reviewer complete"
  required_approvals: []
```

### `waf_reviewer.md`

```yaml
coordination:
  triggers:
    - "user requests WAF review or security assessment"
    - "diagram is approved"
  recommended_hats:
    - "terraform_reviewer"
  parallel_with:
    - "terraform_reviewer"
  handoff_message: "WAF review complete. Terraform generation can proceed."
  synthesis_step: null
  required_approvals: []
```

### `terraform_reviewer.md`

```yaml
coordination:
  triggers:
    - "user requests Terraform or IaC"
    - "architecture is approved"
  recommended_hats: []
  parallel_with:
    - "waf_reviewer"
  handoff_message: "Terraform generation complete."
  synthesis_step: null
  required_approvals: []
```

### `critic.md`

```yaml
coordination:
  triggers: []
  recommended_hats: []
  parallel_with: []
  handoff_message: null
  synthesis_step: null
  required_approvals: []
```

### `governor.md`

```yaml
coordination:
  triggers:
    - "BOM finalisation"
    - "Terraform finalisation"
    - "WAF output finalisation"
  recommended_hats: []
  parallel_with: []
  handoff_message: "Governor review complete. All deterministic checks passed."
  synthesis_step: null
  required_approvals:
    - "cost_overrun"
    - "gpu_confirmation"
```

---

## 2. HatEngine Changes

**File:** `agent/hat_engine.py`

### New public method: `get_coordination_rules(name: str) -> dict`

```python
def get_coordination_rules(self, name: str) -> dict:
    """Return the coordination dict from the named hat's frontmatter, or {}."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return meta.get("coordination", {})
```

### New public method: `get_parallel_hats(name: str) -> list[str]`

```python
def get_parallel_hats(self, name: str) -> list[str]:
    """Return hats that can run in parallel with the named hat."""
    rules = self.get_coordination_rules(name)
    return rules.get("parallel_with", [])
```

### New public method: `get_handoff_message(name: str) -> str | None`

```python
def get_handoff_message(self, name: str) -> str | None:
    rules = self.get_coordination_rules(name)
    return rules.get("handoff_message") or None
```

---

## 3. Forge Changes

**File:** `skillforge/forge.py`

### Coordination trigger check (pre-turn)

In `run_turn`, after the existing `get_transition_suggestions` call (from p35k),
also evaluate coordination rules for **already active hats**. For each active
hat, check its `coordination.triggers` against `user_message`. If a match is
found and `recommended_hats` are not active, emit a status event:

```python
for hat_name in active_hats:
    coord = self._hat_engine.get_coordination_rules(hat_name)
    triggers = coord.get("triggers", [])
    message_lower = user_message.lower()
    for trigger in triggers:
        if any(w in message_lower for w in trigger.lower().split(",")):
            recommended = coord.get("recommended_hats", [])
            inactive = [h for h in recommended if h not in active_hats]
            if inactive:
                yield TurnEvent(
                    type="status",
                    data={"message": f"Coordination: '{hat_name}' suggests activating {inactive}"}
                )
            parallel = self._hat_engine.get_parallel_hats(hat_name)
            inactive_parallel = [h for h in parallel if h not in active_hats]
            if inactive_parallel:
                yield TurnEvent(
                    type="status",
                    data={"message": f"Parallel opportunity: {inactive_parallel} can run alongside '{hat_name}'"}
                )
            break
```

### Handoff message on hat drop

When a hat is dropped (after the p35k `suggested_next_hat` status event), also
check `get_handoff_message` and emit if non-null:

```python
handoff_msg = self._hat_engine.get_handoff_message(hat_name)
if handoff_msg:
    yield TurnEvent(
        type="status",
        data={"message": handoff_msg}
    )
```

### Synthesis step logging

If a hat's `coordination.synthesis_step` is non-null, log it (not yielded as
event — just DEBUG log) when that hat is dropped:

```python
coord = self._hat_engine.get_coordination_rules(hat_name)
synthesis = coord.get("synthesis_step")
if synthesis:
    logger.debug(
        "Hat '%s' dropped; synthesis step pending: %s session=%s",
        hat_name, synthesis, session_id
    )
```

### Hat transition log

At every hat activation (`apply_hat`) and drop (`drop_hat`), log the
transition at INFO level:

```python
logger.info("Hat transition: %s → active_hats=%s session=%s", hat_name, active_hats, session_id)
```

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/hat_engine.py skillforge/forge.py` exits 0
2. `grep "coordination" agent/hats/bom_reviewer.md` — matches
3. `grep "get_coordination_rules" agent/hat_engine.py` — matches
4. `grep "Coordination:" skillforge/forge.py` — matches (in status event)
5. `grep "Hat transition:" skillforge/forge.py` — matches (in INFO log)
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Commit Message

```
p35m: coordination rules frontmatter + parallel/handoff/synthesis events in Forge
```

Branch: `claude/p35m` (from main after p35k and p35l merge). Push when done.

# Task p35k: Hat Stack Roles & Intelligent Transition Rules

## Goal

Skill files can declare `hat_rules` to describe when they should activate,
what they can hand off to, and what condition resumes them. `Forge` reads
these rules before each turn and surfaces transition suggestions.

`p35j` must be merged first — this task depends on the YAML frontmatter
parsing infrastructure (`_parse_hat_file`, `build_expert_block`).

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/hat_engine.py skillforge/forge.py
grep "build_expert_block" agent/hat_engine.py   # p35j must be present
pytest tests/ -q --tb=short 2>&1 | tail -5
```

---

## Scope

**Modify:**
- `agent/hats/*.md` — add `hat_rules` sections to all 6 files
- `agent/hat_engine.py` — add `get_hat_rules()`, `get_transition_suggestions()`
- `skillforge/forge.py` — use suggestions in pre-turn status event

**Do NOT touch:** `archie_wiring.py`, memory modules, handler files.

---

## 1. `hat_rules` YAML Section

Add the following `hat_rules` block to each hat file's existing frontmatter.
Replace the current `hat_rules: {}` placeholder.

### `bom_reviewer.md`

```yaml
hat_rules:
  when_to_activate:
    - "user asks about cost, pricing, BOM, XLSX, or budget"
    - "user requests SKU advice or instance sizing"
    - "BOM generation or repair is requested"
  can_hand_off_to:
    - "diagram_builder"
    - "terraform_reviewer"
    - "waf_reviewer"
  suggested_next_hat: "diagram_builder"
  resume_condition: "cost or sizing questions arise after handoff"
```

### `diagram_builder.md`

```yaml
hat_rules:
  when_to_activate:
    - "user asks for a diagram, architecture drawing, or topology"
    - "user requests diagram update, refinement, or change"
    - "BOM is approved and diagram is next"
  can_hand_off_to:
    - "waf_reviewer"
    - "terraform_reviewer"
    - "bom_reviewer"
  suggested_next_hat: "waf_reviewer"
  resume_condition: "diagram update or correction is requested"
```

### `waf_reviewer.md`

```yaml
hat_rules:
  when_to_activate:
    - "user requests a WAF review, Well-Architected review, or security assessment"
    - "diagram is approved and architecture review is next"
    - "user asks about security posture, compliance, or risk"
  can_hand_off_to:
    - "terraform_reviewer"
    - "bom_reviewer"
  suggested_next_hat: "terraform_reviewer"
  resume_condition: "security or compliance questions arise after handoff"
```

### `terraform_reviewer.md`

```yaml
hat_rules:
  when_to_activate:
    - "user requests Terraform, IaC, or infrastructure-as-code generation"
    - "architecture is approved and IaC is next"
    - "user asks about OCI resource deployment or automation"
  can_hand_off_to:
    - "waf_reviewer"
    - "bom_reviewer"
  suggested_next_hat: null
  resume_condition: "Terraform correction or regeneration is requested"
```

### `critic.md`

```yaml
hat_rules:
  when_to_activate:
    - "a critique_enabled tool has returned a result"
  can_hand_off_to: []
  suggested_next_hat: null
  resume_condition: null
```

### `governor.md`

```yaml
hat_rules:
  when_to_activate:
    - "BOM, Terraform, or WAF output is being finalised"
    - "estimated cost exceeds or approaches stated budget"
    - "public internet exposure is present in the architecture"
  can_hand_off_to: []
  suggested_next_hat: null
  resume_condition: "finalisation of any deliverable resumes governor review"
```

---

## 2. HatEngine Changes

**File:** `agent/hat_engine.py`

### New public method: `get_hat_rules(name: str) -> dict`

```python
def get_hat_rules(self, name: str) -> dict:
    """Return the hat_rules dict from the named hat's frontmatter, or {}."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return meta.get("hat_rules", {})
```

### New public method: `get_transition_suggestions(active_hats, turn_message) -> list[str]`

```python
def get_transition_suggestions(
    self, active_hats: list[str], turn_message: str
) -> list[str]:
    """
    Scan hat_rules.when_to_activate for all registered hats.
    Return names of hats not already active whose trigger phrases appear
    (case-insensitive) in turn_message.
    """
    message_lower = turn_message.lower()
    suggestions: list[str] = []
    for name in self._hat_cache:
        if name in active_hats:
            continue
        rules = self.get_hat_rules(name)
        triggers = rules.get("when_to_activate", [])
        for trigger in triggers:
            if any(word in message_lower for word in trigger.lower().split(",")):
                suggestions.append(name)
                break
    return suggestions
```

### New public method: `get_suggested_next_hat(name: str) -> str | None`

```python
def get_suggested_next_hat(self, name: str) -> str | None:
    """Return the suggested_next_hat from the named hat's rules, or None."""
    rules = self.get_hat_rules(name)
    return rules.get("suggested_next_hat") or None
```

---

## 3. Forge Changes

**File:** `skillforge/forge.py`

### Pre-turn transition check

At the start of `run_turn`, after receiving the `user_message` argument and
before the ReAct loop, call `get_transition_suggestions` and emit a status
event if non-empty suggestions are found:

```python
# Before ReAct loop
suggestions = self._hat_engine.get_transition_suggestions(active_hats, user_message)
if suggestions:
    suggestion_names = ", ".join(suggestions)
    yield TurnEvent(
        type="status",
        data={"message": f"Suggested hats for this request: {suggestion_names}"}
    )
```

### Hat drop → suggest next hat

When a hat is dropped (LLM calls `drop_hat_{name}`), check `get_suggested_next_hat`
and if non-null, emit a status event:

```python
next_hat = self._hat_engine.get_suggested_next_hat(hat_name)
if next_hat:
    yield TurnEvent(
        type="status",
        data={"message": f"Hat '{hat_name}' dropped. Suggested next: '{next_hat}'."}
    )
```

### Hat stack role tracking

The hat stack remains a `list[str]`. Document the convention in a comment:
- `active_hats[0]` = primary (first activated)
- `active_hats[-1]` = most recently activated

No structural change to the list is needed; this is for caller reference.

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/hat_engine.py skillforge/forge.py` exits 0
2. `grep "hat_rules" agent/hats/bom_reviewer.md` — matches
3. `grep "get_transition_suggestions" agent/hat_engine.py` — matches
4. `grep "get_hat_rules" agent/hat_engine.py` — matches
5. `grep "Suggested hats" skillforge/forge.py` — matches
6. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Commit Message

```
p35k: hat_rules frontmatter + transition suggestions in HatEngine and Forge
```

Branch: `claude/p35k` (from main after p35j merges). Push when done.

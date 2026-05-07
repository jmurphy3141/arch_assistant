# Task p35l: Hat-Specific Memory Views

## Goal

When an expert hat is active, inject a tailored, role-optimised view of memory
into the LLM call rather than the raw full snapshot. The orchestrator retains
the full canonical memory for coordination. Hat files declare what to focus on
via a `memory_focus` YAML section.

`p35j` must be merged first — this task depends on `_parse_hat_file`.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/hat_engine.py skillforge/forge.py skillforge/memory.py
grep "_parse_hat_file" agent/hat_engine.py   # p35j must be present
pytest tests/ -q --tb=short 2>&1 | tail -5
```

---

## Scope

**Modify:**
- `agent/hats/*.md` — add `memory_focus` sections to all 6 files
- `agent/hat_engine.py` — add `get_memory_focus()`, `build_memory_view_block()`
- `skillforge/forge.py` — inject filtered memory block into user prompt when
  hats are active

**Do NOT touch:** `skillforge/memory.py`, `archie_wiring.py`, handler files.

---

## 1. `memory_focus` YAML Section

Add the following `memory_focus` block to each hat file's frontmatter.
Replace the current `memory_focus: {}` placeholder.

### `bom_reviewer.md`

```yaml
memory_focus:
  priority_fields:
    - "sizing"
    - "compute_shapes"
    - "storage_requirements"
    - "workloads"
    - "cost_assumptions"
    - "budget"
    - "region"
  summary_style: "cost_and_sizing_oriented"
  include_full_memory: false
  emphasis: >
    Focus heavily on quantities, OCPU/memory sizing, storage volumes, pricing
    assumptions, and budget constraints. Highlight any sizing gaps.
```

### `diagram_builder.md`

```yaml
memory_focus:
  priority_fields:
    - "components"
    - "topology"
    - "subnet_tiers"
    - "gateways"
    - "connectivity"
    - "ha_dr_mode"
    - "data_flows"
  summary_style: "topology_oriented"
  include_full_memory: false
  emphasis: >
    Focus on network topology, component placement, traffic paths, security
    boundaries, and HA/DR mode. Highlight connectivity and exposure requirements.
```

### `waf_reviewer.md`

```yaml
memory_focus:
  priority_fields:
    - "public_exposure"
    - "security_controls"
    - "compliance_requirements"
    - "topology"
    - "data_classification"
    - "dr_posture"
  summary_style: "security_and_risk_oriented"
  include_full_memory: false
  emphasis: >
    Focus on exposure, security controls, compliance gaps, DR posture, and
    observability. Highlight risks and unresolved security decisions.
```

### `terraform_reviewer.md`

```yaml
memory_focus:
  priority_fields:
    - "resources"
    - "compartments"
    - "naming_conventions"
    - "tagging_requirements"
    - "state_backend"
    - "security_constraints"
    - "region"
  summary_style: "iac_oriented"
  include_full_memory: false
  emphasis: >
    Focus on resource dependencies, compartment structure, naming/tagging rules,
    state management, and security constraints for generated IaC.
```

### `critic.md`

```yaml
memory_focus:
  priority_fields: []
  summary_style: "full"
  include_full_memory: true
  emphasis: "Critic needs full context to evaluate correctness against original request."
```

### `governor.md`

```yaml
memory_focus:
  priority_fields:
    - "budget"
    - "cost_assumptions"
    - "public_exposure"
    - "compliance_requirements"
    - "gpu_shapes"
  summary_style: "governance_oriented"
  include_full_memory: false
  emphasis: >
    Focus on cost posture, budget targets, public exposure, GPU usage, and
    compliance requirements. Flag any deterministic blocks immediately.
```

---

## 2. HatEngine Changes

**File:** `agent/hat_engine.py`

### New public method: `get_memory_focus(name: str) -> dict`

```python
def get_memory_focus(self, name: str) -> dict:
    """Return the memory_focus dict from the named hat's frontmatter, or {}."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return meta.get("memory_focus", {})
```

### New public method: `build_memory_view_block(name: str, memory_snapshot) -> str`

```python
def build_memory_view_block(self, name: str, memory_snapshot) -> str:
    """
    Build a labelled memory view block for injection into the user prompt.

    If memory_snapshot is None or empty, returns an empty string.
    If include_full_memory is True, returns the full snapshot with label.
    Otherwise, returns priority_fields only.
    """
    if memory_snapshot is None:
        return ""

    focus = self.get_memory_focus(name)
    if not focus:
        return ""

    display = self.get_hat_meta(name).get("display_name", name)
    priority = focus.get("priority_fields", [])
    include_full = focus.get("include_full_memory", False)
    emphasis = focus.get("emphasis", "")

    lines = [f"[MEMORY VIEW FOR {display.upper()}]"]
    if emphasis:
        lines.append(emphasis.strip())
    lines.append("")

    raw = getattr(memory_snapshot, "raw", {}) or {}

    if include_full or not priority:
        if raw:
            for k, v in raw.items():
                lines.append(f"{k}: {v}")
    else:
        found_any = False
        for field in priority:
            for k, v in raw.items():
                if field.lower() in k.lower() and v:
                    lines.append(f"{k}: {v}")
                    found_any = True
        if not found_any:
            lines.append("(No relevant facts yet recorded for this focus area.)")

    lines.append(f"[End MEMORY VIEW FOR {display.upper()}]")
    return "\n".join(lines)
```

### New public method: `get_hat_meta(name: str) -> dict`

```python
def get_hat_meta(self, name: str) -> dict:
    """Return the parsed frontmatter metadata dict for the named hat."""
    path = _hat_path(name)
    if path is None:
        return {}
    meta, _, _ = _parse_hat_file(path)
    return meta
```

---

## 3. Forge Changes

**File:** `skillforge/forge.py`

In `run_turn`, when the memory snapshot is assembled (after `memory.assemble()`
call), and active hats are non-empty, build memory view blocks and prepend to
the user prompt:

```python
# After assembling memory snapshot, before first LLM call in the ReAct loop
if active_hats and memory_snapshot is not None:
    view_blocks = []
    for hat_name in active_hats:
        block = self._hat_engine.build_memory_view_block(hat_name, memory_snapshot)
        if block:
            view_blocks.append(block)
    if view_blocks:
        memory_prefix = "\n\n".join(view_blocks) + "\n\n"
    else:
        memory_prefix = ""
else:
    memory_prefix = ""
```

When constructing `prompt_for_llm`, prepend `memory_prefix`:

```python
prompt_for_llm = memory_prefix + self._hat_engine.inject_hats(enriched, active_hats)
```

**Note:** The memory view block is injected into the **user prompt** (not the
system message). Expert block stays in the system message (`_build_active_system_msg`).
This keeps them separate and independently cacheable.

After each `memory_contract` tool call that updates the snapshot, rebuild
`memory_prefix` for the next loop iteration.

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/hat_engine.py skillforge/forge.py` exits 0
2. `grep "memory_focus" agent/hats/bom_reviewer.md` — matches
3. `grep "build_memory_view_block" agent/hat_engine.py` — matches
4. `grep "MEMORY VIEW" skillforge/forge.py` — matches (in the view block builder)
5. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before
6. Manual check: `build_memory_view_block("bom_reviewer", snapshot)` returns a
   string containing `[MEMORY VIEW FOR BOM EXPERT]` when snapshot has non-empty `raw`.

---

## Commit Message

```
p35l: memory_focus frontmatter + hat-specific memory view injection
```

Branch: `claude/p35l` (from main after p35j merges, parallel with p35k). Push when done.

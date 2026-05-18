# Task p44a: Enrich Archie System Prompt with Sequencing Rules

## Objective

`archie_loop.py` hardcodes all tool sequencing logic (BOM before diagram,
diagram before WAF/Terraform, POV+JEP can run together). Before we can remove
those routing blocks in p44b, the Archie system prompt must carry those rules
so Forge's LLM makes the same decisions.

This task is purely additive — no existing code is removed.

---

## Scope

**Touch:**
- `agent/archie_wiring.py` — update `ORCHESTRATOR_SYSTEM_MSG` or the
  `base_system_prompt` block passed to `Forge()`

**Do NOT touch:** `archie_loop.py`, `skillforge/`, hat files, test files.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_wiring.py
grep "BOM before\|diagram before\|POV.*JEP\|sequencing\|prerequisite" \
  agent/archie_wiring.py
# must be zero matches — we are adding these rules fresh
```

---

## Changes

### `agent/archie_wiring.py` — system prompt additions

Find the `base_system_prompt` or `ORCHESTRATOR_SYSTEM_MSG` string passed to
`Forge(base_system_prompt=...)`. Append the following section to it:

```
## Tool Sequencing Rules

These rules are mandatory. Follow them on every generation request.

### Ordering
1. When the user requests both a BOM and a diagram in the same turn, always
   call generate_bom FIRST. Pass the BOM result payload to generate_diagram.
2. generate_waf and generate_terraform both require an existing diagram.
   If no diagram exists for the customer, generate one first.
3. generate_pov and generate_jep can be requested in the same turn and may
   be called sequentially in one turn.

### Single-tool requests
4. If the user asks only for a BOM, call generate_bom once and return.
5. If the user asks only for a diagram, call generate_diagram once and return.
6. Do not generate unrequested deliverables.

### Artifact re-use
7. If the user asks for a download link or asks to view an existing artifact,
   return the artifact key from context — do not re-generate.

### Update requests
8. If the user says "update everything" or "regenerate all", identify which
   tools have existing artifacts in context and re-run them in this order:
   generate_bom → generate_diagram → generate_waf → generate_terraform →
   generate_pov → generate_jep (skip any that were not previously generated).
```

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall agent/archie_wiring.py
   ```

2. Rules present:
   ```bash
   grep "BOM.*FIRST\|generate_bom.*generate_diagram\|diagram.*WAF\|update.*order" \
     agent/archie_wiring.py | wc -l
   # must be >= 3
   ```

3. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p44a: enrich Archie system prompt — tool sequencing rules for BOM→diagram→WAF order
```

Branch: `claude/p44a` (from main). Push when done.

# Task p43d: Correction Propagation to Sub-Agents

## Objective

When `_run_expert_post_review()` returns `"iterate"`, p41b appends:

```
CORRECTION REQUIRED: The expert review found a fixable problem with the last
'generate_diagram' call. Specifically: <concern>
Re-call 'generate_diagram' now with corrected arguments...
```

The manager re-calls the tool. But `tool_args` — what actually reaches the
diagram/BOM/WAF sub-agent — comes from the LLM's next JSON output, not from
the correction text. The sub-agent gets the same instructions it had before.
The iterate loop fires but the correction never reaches the work.

Fix: track the pending correction as a local variable in `run_turn()`. When
the same tool is re-dispatched on the next iteration, Forge injects
`_forge_correction` into `tool_args`. Each handler passes it to the sub-agent
prompt, guaranteeing the correction reaches the worker.

---

## Scope

**Touch:**
- `skillforge/forge.py` — add `_pending_correction` tracking in `run_turn()`
- `agent/tools/diagram.py` — prepend `_forge_correction` to sub-agent prompt
- `agent/tools/bom.py` — same
- `agent/tools/specialists.py` — same (WafHandler, PovHandler, JepHandler)

**Do NOT touch:** hat files, test files, `skillforge/registry.py`, other modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py agent/tools/diagram.py \
  agent/tools/bom.py agent/tools/specialists.py
grep "_pending_correction\|_forge_correction" skillforge/forge.py \
  agent/tools/diagram.py agent/tools/bom.py agent/tools/specialists.py
# must be zero matches
```

---

## Changes

### 1. `skillforge/forge.py` — `run_turn()` correction tracking

At the top of `run_turn()`, after other local variable initialisations, add:

```python
        _pending_correction: dict | None = None   # tool name + concern for next re-call
```

In the `iterate` path (after the `CORRECTION REQUIRED` prompt block, before
`continue`), add:

```python
                    _pending_correction = {
                        "tool": tool_name,
                        "concern": _iterate_concern,
                    }
                    continue
```

In the domain tool dispatch section, immediately after the `requires_hat`
auto-activation block and BEFORE the `_run_expert_pre_action` call, add:

```python
            # ── Correction injection ──────────────────────────────────────────
            if (
                _pending_correction is not None
                and _pending_correction.get("tool") == tool_name
            ):
                concern = _pending_correction["concern"]
                if concern:
                    task_key = "prompt" if "prompt" in tool_args else "task"
                    tool_args = {
                        **tool_args,
                        "_forge_correction": concern,
                    }
                    logger.info(
                        "[FORGE] Injecting correction into '%s' args session=%s: %s",
                        tool_name, session_id, concern,
                    )
                _pending_correction = None
```

### 2. `agent/tools/diagram.py`

In `DiagramHandler.__call__`, before calling `_call_generate_diagram`, extract
and prepend any correction:

```python
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            existing_prompt = str(args.get("prompt") or "")
            args = {
                **args,
                "prompt": (
                    f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                    f"{existing_prompt}"
                ).strip(),
            }
```

### 3. `agent/tools/bom.py`

In `BomHandler.__call__`, before the sub-agent call, extract and prepend:

```python
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            existing = str(args.get("prompt") or "")
            args = {
                **args,
                "prompt": (
                    f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                    f"{existing}"
                ).strip(),
            }
```

### 4. `agent/tools/specialists.py`

In `_SpecialistHandler.__call__`, before `raw_request` is built or passed
to the sub-agent, extract and prepend:

```python
        correction = str(args.pop("_forge_correction", "") or "").strip()
        if correction:
            raw_request = (
                f"[CORRECTION FROM EXPERT REVIEW: {correction}]\n\n"
                f"{raw_request}"
            ).strip()
```

(Applies to WafHandler, PovHandler, JepHandler via the shared base class.)

---

## Acceptance Criteria

1. Compiles cleanly:
   ```bash
   python3.11 -m compileall skillforge/forge.py agent/tools/diagram.py \
     agent/tools/bom.py agent/tools/specialists.py
   ```

2. Correction symbols present:
   ```bash
   grep "_pending_correction\|_forge_correction\|CORRECTION FROM EXPERT" \
     skillforge/forge.py agent/tools/diagram.py agent/tools/bom.py \
     agent/tools/specialists.py | wc -l
   # must be ≥ 6
   ```

3. No regressions:
   ```bash
   pytest tests/test_forge.py tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p43d: correction propagation — inject expert review concern into sub-agent prompt on iterate
```

Branch: `claude/p43d` (from main, after p43a–p43c merged). Push when done.

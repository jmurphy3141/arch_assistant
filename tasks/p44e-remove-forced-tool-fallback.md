# Task p44e: Remove Forced-Tool Fallback — Let Forge Fail Loudly

## Objective

Remove the `_single_requested_tool_to_force()` fallback from `archie_session.py`
and strengthen the Archie system prompt so Forge's LLM reliably calls generation
tools as tool-call JSON rather than returning prose.

**IMPORTANT:** Branch from main (all p44a–p44d must be merged first).

---

## Background

Codex's p44b introduced a safety net at `archie_session.py` lines 635–640:

```python
forced_tool = _single_requested_tool_to_force(requested_tools, tool_calls)
if forced_tool:
    call = await _run_generation_step(
        forced_tool,
        _default_generation_tool_args(forced_tool, user_message),
    )
```

When Forge's LLM returns text instead of a tool call, this block detects which
tool was expected and invokes it directly via `_run_generation_step()` →
`forge.invoke_tool()`. It bypasses every piece of expert reasoning that p39–p43
built:

- **requires_hat gate** never fires → hat never activates
- **expert pre_action** never fires → no expert guidance for the sub-agent
- **expert post_review** never fires → no quality check on the result
- **correction loop** never fires → bad output goes straight to the user

The `_single_requested_tool_to_force()` and `_default_generation_tool_args()`
helpers at lines 2621–2638 exist only to support this bypass.

The real fix is two parts:
1. Remove the fallback so failures are visible (missing tool call = empty reply,
   which Forge will handle gracefully).
2. Strengthen the system prompt so the LLM reliably calls the tool as JSON
   instead of returning prose about what it's going to do.

---

## Scope

**Touch:**
- `agent/archie_session.py` — remove fallback block + two helpers
- `agent/archie_wiring.py` — add explicit tool-call discipline rules to `_TOOL_SEQUENCING_RULES`

**Do NOT touch:** `skillforge/`, hat files, other test files, other modules.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_session.py agent/archie_wiring.py
grep -n "_single_requested_tool_to_force\|forced_tool\|_default_generation_tool_args" \
  agent/archie_session.py | wc -l
# note the count — must drop to 0 after the edit
grep "You MUST output.*tool call\|NEVER respond with prose\|tool-call discipline" \
  agent/archie_wiring.py | wc -l
# must be 0 before — non-zero after
```

---

## Changes

### 1. `agent/archie_session.py` — remove fallback block

**Remove lines 635–656** (the entire `forced_tool` block):

```python
    forced_tool = _single_requested_tool_to_force(requested_tools, tool_calls)
    if forced_tool:
        call = await _run_generation_step(
            forced_tool,
            _default_generation_tool_args(forced_tool, user_message),
        )
        reply_text = str(call.get("result_summary", "") or "").strip()
        if forced_tool == "generate_diagram":
            reply_text = _build_single_diagram_reply(call, decision_context=decision_context)
        elif forced_tool == "generate_bom":
            data = call.get("result_data", {}) if isinstance(call.get("result_data"), dict) else {}
            if archie_memory._bom_call_was_memory_revision(data) and "BOM revision was performed" not in reply_text:
                reply_text = f"BOM revision was performed from updated memory.\n\n{reply_text}".strip()
            section = _bom_resolved_inputs_reply_section(data)
            if section:
                reply_text = "\n".join([reply_text or "Final BOM prepared.", *section]).strip()
        reply = _append_management_summary(
            reply_text or f"Completed `{forced_tool}`.",
            tool_calls,
            decision_context=decision_context,
        )
```

After removal, line 635 should be:

```python
    return _finalize_turn(reply)
```

**Remove helper functions** at the bottom of the file:

```python
def _single_requested_tool_to_force(requested_tools: set[str], tool_calls: list[dict[str, Any]]) -> str:
    if len(requested_tools) != 1:
        return ""
    tool_name = next(iter(requested_tools))
    if any(call.get("tool") == tool_name for call in tool_calls):
        return ""
    return tool_name

def _default_generation_tool_args(tool_name: str, user_message: str) -> dict[str, Any]:
    text = str(user_message or "").strip()
    if tool_name == "generate_diagram":
        return {"bom_text": text}
    if tool_name == "generate_bom":
        return {"prompt": text}
    if tool_name == "generate_terraform":
        return {"prompt": text}
    if tool_name in {"generate_pov", "generate_jep", "generate_waf"}:
        return {"feedback": text}
    return {}
```

Verify both functions have no other callers before deleting:

```bash
grep -n "_single_requested_tool_to_force\|_default_generation_tool_args" \
  agent/archie_session.py
# must return 0 lines after removal
```

### 2. `agent/archie_wiring.py` — tool-call discipline rules

Append a new section to `_TOOL_SEQUENCING_RULES` (inside the triple-quoted string,
after the existing 8 rules):

```python
### Tool-call discipline (mandatory)
9. You MUST output a tool-call JSON line for every generation request. Never
   respond with prose describing what you are about to do. Prose responses are
   ONLY for conversational turns where no tool is needed.
   Correct: {"tool": "generate_bom", "args": {"prompt": "..."}}
   Wrong: "I'll generate a BOM for your web service architecture now."

10. After step3_planning, if the plan identifies a generation action, immediately
    output the tool-call JSON. Do not narrate the plan — execute it.

11. The tool-call JSON must appear alone on a single line with no surrounding text.
    If you need to say something to the user as well, wait until after the tool
    result is returned — Forge will give you another turn.
```

---

## Acceptance Criteria

1. No fallback references remain:
   ```bash
   grep -rn "_single_requested_tool_to_force\|_default_generation_tool_args\|forced_tool" \
     agent/archie_session.py | grep -v ".pyc" | wc -l
   # must be 0
   ```

2. Files compile cleanly:
   ```bash
   python3.11 -m compileall agent/archie_session.py agent/archie_wiring.py
   ```

3. Tool-call discipline rules present:
   ```bash
   grep "You MUST output.*tool-call\|You MUST output a tool-call" agent/archie_wiring.py | wc -l
   # must be 1
   ```

4. Architecture test still passes:
   ```bash
   pytest tests/test_archie_forge_wiring.py -v --tb=short
   # all 5 parametrize cases must pass
   ```

5. No regressions:
   ```bash
   pytest tests/ -q --tb=short -m "not live" -x
   ```

---

## Commit Message

```
p44e: remove forced-tool fallback — Forge expert reasoning now fires on all generation requests
```

Branch: `claude/p44e` (from main, after p44d merged). Push when done.

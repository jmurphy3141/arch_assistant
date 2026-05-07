# Task: Wire hat engine into archie_loop.py
Phase: 3
Status: todo
Depends on: p3-hat-engine.md merged to main

## Goal
Wire `agent/hat_engine.py` into `agent/archie_loop.py`'s `run_turn()` so that:
- Archie's tool list includes `use_hat_X` and `drop_hat_X` for every hat
- When Archie calls `use_hat_X`, the hat is added to the active stack
- Active hat content is injected at the start of each prompt round
- When Archie calls `drop_hat_X`, the hat is removed from the active stack

Then delete `agent/critic_agent.py` and `agent/governor_agent.py`.

---

## Prerequisite
`agent/hat_engine.py` and `agent/hats/*.md` must already exist (from p3-hat-engine.md).
`agent/safety_rules.py` must already exist.

---

## Files to modify

### `agent/archie_loop.py`

#### 1. Add imports
Near the top:
```python
import agent.hat_engine as hat_engine
import agent.safety_rules as safety_rules
```

#### 2. Inside `run_turn()` — initialise hat state
At the very start of `run_turn()`, before the conversation loop:
```python
_active_hats: list[str] = []
```

#### 3. Inside `run_turn()` — add hat tools to the tool list
Wherever the tool list is assembled for the LLM call, append:
```python
hat_tools = hat_engine.get_hat_tool_definitions()
# add hat_tools to the existing tool list before passing to the LLM
```

#### 4. Inside `run_turn()` — handle hat tool calls
In the section where tool calls are dispatched (after the LLM returns a
tool name), add a handler before the existing tool dispatch:

```python
if tool_name.startswith("use_hat_"):
    hat_name = tool_name[len("use_hat_"):]
    if hat_name in hat_engine.load_hats() and hat_name not in _active_hats:
        _active_hats.append(hat_name)
    # Return a simple acknowledgement — no sub-agent call
    tool_result_summary = f"Hat '{hat_name}' activated."
    tool_artifact_key = ""
    tool_result_data = {"hat": hat_name, "action": "activated"}
    # continue to next iteration without calling sub-agents
    continue  # or equivalent control flow

elif tool_name.startswith("drop_hat_"):
    hat_name = tool_name[len("drop_hat_"):]
    if hat_name in _active_hats:
        _active_hats.remove(hat_name)
    tool_result_summary = f"Hat '{hat_name}' deactivated."
    tool_artifact_key = ""
    tool_result_data = {"hat": hat_name, "action": "deactivated"}
    continue  # or equivalent control flow
```

#### 5. Inside `run_turn()` — inject active hats into each prompt round
In the prompt assembly section (where the prompt string for the LLM is built),
wrap the assembled prompt with hat injection:

```python
prompt = hat_engine.inject_hats(prompt, _active_hats)
```

This must happen after the prompt is assembled and before it is passed to
the `text_runner`.

#### 6. Inside `run_turn()` — apply safety_rules after tool completion
After each tool returns a result and before the result is presented to the
user, call:

```python
safe, reason = safety_rules.check(tool_name, tool_result_data)
if not safe:
    tool_result_summary = f"[Safety block] {reason}"
    tool_result_data = {"safety_block": True, "reason": reason}
```

---

## Files to delete
- `agent/critic_agent.py`
- `agent/governor_agent.py`

Before deleting, confirm no file outside the deleted ones imports them:
```
grep -rn "from agent.critic_agent\|from agent.governor_agent\|import critic_agent\|import governor_agent" \
    --include="*.py" . \
    | grep -v "critic_agent.py\|governor_agent.py"
```
If any live import is found that is NOT in a test file for those agents,
resolve it before deleting. If only test files reference them, delete the
test files for those agents too:
- `tests/test_critic_agent.py` (if it exists and only tests the deleted module)
- `tests/test_governor_agent.py` (if it exists and only tests the deleted module)

---

## Files to NOT touch
- `agent/hat_engine.py` (already correct from previous task)
- `agent/hats/*.md` (already correct from previous task)
- `agent/safety_rules.py` (already correct from previous task)
- Any file in `sub_agents/`
- `drawing_agent_server.py`

---

## What to do
1. Modify `archie_loop.py` as described above.
2. Run `python3.11 -m compileall agent/archie_loop.py` — fix any import errors.
3. Check for dead imports of `critic_agent` and `governor_agent` as described.
4. Delete `agent/critic_agent.py` and `agent/governor_agent.py`.
5. Delete the test files for those agents if they only test the deleted modules.
6. Run the full test suite.

---

## Acceptance criteria
- `python3.11 -m compileall agent/archie_loop.py` exits 0
- `python3.11 -c "import agent.archie_loop"` exits 0 (no import errors)
- `ls agent/critic_agent.py agent/governor_agent.py` returns "No such file" for both
- `grep -rn "use_hat_\|drop_hat_" agent/archie_loop.py` returns matches (hats are handled)
- `grep -rn "inject_hats" agent/archie_loop.py` returns a match (injection is wired)
- `grep -rn "safety_rules.check" agent/archie_loop.py` returns a match
- A manual smoke-test: create a minimal Python script that calls `run_turn()` with
  a mocked `text_runner` that returns `'{"tool": "use_hat_critic", "args": {}}'`
  on the first call and `"Review complete."` on the second; verify the result
  contains `"reply"` and no exception is raised.
- `pytest tests/ -v -m "not live"` — no new failures beyond the 4 pre-existing ones

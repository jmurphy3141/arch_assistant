# Task p2a: Forge Unit Tests

## Goal

Write unit tests for `skillforge.Forge` and a non-OCI smoke test proving
domain-agnostic reuse. No production code changes. No OCI imports in tests.

## Prerequisite Check

```bash
python3.11 -c "from skillforge import Forge, ToolResult, MemorySnapshot; print('ok')"
```

Then verify the format instruction was added (p2a0 must be merged first):

```bash
python3.11 -c "
from skillforge.forge import _assemble_system_prompt
from skillforge.registry import ToolRegistry
msg = _assemble_system_prompt('base', [], ToolRegistry())
assert 'Tool call format' in msg, 'p2a0 not applied — merge p2a0 first'
print('ok')
"
```

If either fails, stop and report. Do not proceed.

## Scope

**Only create these files:**

- `tests/test_forge.py`
- `tests/test_forge_aws.py`

**Do NOT touch any existing file.**

## What to implement

### `tests/test_forge.py`

Use `pytest` with `pytest.mark.asyncio`. All dependencies are mocked:

- `hat_engine`: a simple object with:
  - `get_hat_tool_definitions()` returning `[]`
  - `apply_hat(active, name)` returning `active + [name]`
  - `drop_hat(active, name)` returning `[h for h in active if h != name]`
  - `warn_stale_hats(active, rounds, max_rounds=5)` returning `[]`
  - `inject_hats(prompt, active)` returning `prompt`
- `memory`: an object whose:
  - `assemble()` returns `MemorySnapshot(session_id="s1")`
  - `update()` returns the input context unchanged
- `text_runner`: an async function that returns a configurable string

Create a `make_forge(text_runner)` helper that returns a configured `Forge`
with one registered tool `"test_tool"` whose handler returns
`ToolResult(summary="done", status="ok", artifact_key="key/1")`.

Write these tests:

1. `test_plain_reply`
   text_runner returns `"Here is my answer"` (no JSON tool call).
   Assert `result.reply == "Here is my answer"` and `result.tool_calls == []`.

2. `test_tool_call_ok`
   text_runner returns `'{"tool": "test_tool", "args": {}}'` on first call,
   then `"Done."` on second call.
   Assert `result.artifacts == {"test_tool": "key/1"}` and `len(result.tool_calls) == 1`.

3. `test_needs_input_surfaces`
   Register a tool whose handler returns
   `ToolResult(summary="x", status="needs_input", clarification="Please provide region.")`.
   text_runner returns the tool call.
   Assert `result.reply == "Please provide region."`.

4. `test_needs_input_retry`
   Same handler as above but register with `retry_on_needs_input=True`.
   text_runner returns tool call on iteration 0, then `"Got it."` on iteration 1.
   Assert `result.reply == "Got it."` (retry happened).

5. `test_blocked_removes_artifact`
   Handler returns `ToolResult(summary="blocked", status="blocked")`.
   Assert `result.artifacts == {}`.

6. `test_safety_check_blocks`
   Register tool with `safety_checker=lambda name, r: (False, "too expensive")`.
   Handler returns `ToolResult(summary="ok", status="ok", artifact_key="k")`.
   Assert `result.artifacts == {}` and `"too expensive"` in `result.tool_calls[-1].result.summary`.

7. `test_hat_activation`
   text_runner returns `'{"tool": "use_hat_critic", "args": {}}'` then `"Done."`.
   Use a mock hat_engine that tracks `apply_hat` calls.
   Verify `apply_hat` was called with `([], "critic")`.

8. `test_skill_guidance_injected`
   Register tool with `skill_guidance="## Guidance\nBe precise."`, `memory_contract=False`.
   Capture the `args` received by the handler.
   Assert `args.get("prompt", "") or args.get("task", "")` starts with `"## Guidance"`.

9. `test_memory_refreshed_after_tool`
   Register tool with `memory_contract=True`.
   Handler returns `ToolResult(summary="ok", status="ok")`.
   Use a mock memory that tracks `update` calls.
   Assert `memory.update` was called once after the tool ran.

10. `test_unknown_tool_continues`
    text_runner returns `'{"tool": "nonexistent", "args": {}}'` then `"Fallback reply."`.
    Assert `result.reply == "Fallback reply."` (loop continued past unknown tool).

11. `test_system_prompt_cached`
    Call `forge._get_system_msg()` twice.
    Assert both calls return the same object (`is`).

12. `test_max_iterations_exhausted`
    text_runner always returns `'{"tool": "test_tool", "args": {}}'` (never a plain reply).
    Handler always returns `ToolResult(summary="ok", status="ok")`.
    With `max_iterations=3`, assert the loop terminates and returns a `TurnResult`.

13. `test_format_instruction_in_system_prompt`
    Create a Forge with one registered tool.
    Call `forge._get_system_msg()`.
    Assert the string `'{"tool"'` appears in the system prompt.
    Assert `"Tool call format"` appears in the system prompt.
    This verifies p2a0's format instruction is present and the LLM will know the
    exact JSON format to emit.

14. `test_critique_enabled_stored`
    Register a tool with `critique_enabled=True`.
    Assert `forge._registry.get("tool_name").critique_enabled is True`.
    (No behavior test yet — behavior is implemented in a follow-on task.)

### `tests/test_forge_aws.py`

Prove Forge works with zero OCI imports. This file must not import anything
from `agent/`.

Define inline:
- `AWSMemory` (implements `assemble` and `update` with dummy data)
- `ec2_handler(args, *, memory, context, trace_id)` returning
  `ToolResult(summary="EC2 sized", status="ok")`
- `cfn_handler(args, *, memory, context, trace_id)` that returns
  `ToolResult(summary="needs arch", status="needs_input", clarification="Define architecture first.")`
  when `memory.facts.get("architecture_defined")` is falsy
- A fake `hat_engine` module-style object (same shape as in test_forge.py)
- A fake `text_runner` that first returns `'{"tool": "size_ec2", "args": {}}'`
  then returns `"EC2 sizing complete."`

Write one test:

`test_aws_forge_end_to_end`
  Wire up a Forge with `AWSMemory`, two tools, and the fake text_runner.
  Run `forge.run_turn(session_id="aws-1", user_message="Size my app", context={})`.
  Assert `result.reply == "EC2 sizing complete."` and
  `"size_ec2" in result.artifacts or len(result.tool_calls) == 1`.

## Acceptance Criteria

1. `pytest tests/test_forge.py -v` — 14 passed, 0 failed
2. `pytest tests/test_forge_aws.py -v` — 1 passed, 0 failed
3. `grep -r "from agent" tests/test_forge_aws.py` — no matches
4. `pytest tests/test_specialist_mode_routing.py -v` — no regressions

## Do NOT Do

- Do not modify any existing file
- Do not import from `agent/` in `test_forge_aws.py`
- Do not use `unittest.mock.patch` on `skillforge/` internals — test through the public API
- Do not skip the regression check

## Commit Message

```
p2a: add Forge unit tests and non-OCI domain smoke test
```

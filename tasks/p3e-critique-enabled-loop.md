# Task p3e: Implement critique_enabled Post-Tool Critic Loop in Forge

## Goal

When a tool is registered with `critique_enabled=True`, Forge should
automatically run a critic review pass after the tool result is appended to
the prompt, before continuing the ReAct loop. The critic uses the active
hat system (specifically `critic` hat) to evaluate the result and either
approve it or inject a revision request.

The `critique_enabled` flag already exists in `ToolRegistry` and is stored per
tool. This task makes it do something in `forge.run_turn()`.

---

## Background: how critique_enabled should work

After a `critique_enabled` tool returns `status="ok"`:

1. Activate the `critic` hat temporarily
2. Make one additional LLM call with a critic prompt asking it to evaluate the
   tool result (pass or flag)
3. If the LLM responds with `{"tool": "critic_approve", "args": {}}` — deactivate
   the critic hat and continue the loop normally
4. If the LLM responds with plain text (critique) — append the critique to the
   prompt as a `CRITIC:` note and continue (the next iteration sees the critique
   and can decide to regenerate)
5. Either way, deactivate the critic hat after the review

The critic does not loop — it fires once per `critique_enabled` tool call.

---

## Prerequisite Check

```bash
python3.11 -m compileall skillforge/forge.py skillforge/registry.py
pytest tests/test_forge.py -v --tb=short 2>&1 | tail -3
```

Both must pass.

Also verify `critic` hat exists:
```bash
ls agent/hats/critic.md
```

---

## Scope

**Only modify:**

- `skillforge/forge.py`

**Only create:**

- `tests/test_forge_critique.py`

**Do NOT touch `agent/`, `skillforge/registry.py`, or any other file.**

---

## What to implement

### In `forge.run_turn()`, after `prompt = _append_result(...)` for domain tools

Replace the current single-line append:
```python
prompt = _append_result(prompt, tool_name, result.summary)
```

With:
```python
prompt = _append_result(prompt, tool_name, result.summary)

# Post-tool critic pass
if spec.critique_enabled and result.status == "ok":
    prompt, active_hats = await self._run_critique_pass(
        prompt=prompt,
        tool_name=tool_name,
        result=result,
        active_hats=active_hats,
        system_msg=system_msg,
        session_id=session_id,
    )
```

### Add `_run_critique_pass()` to `Forge`

```python
async def _run_critique_pass(
    self,
    *,
    prompt: str,
    tool_name: str,
    result: ToolResult,
    active_hats: list[str],
    system_msg: str,
    session_id: str,
) -> tuple[str, list[str]]:
    """
    Fire a single critic review after a critique_enabled tool returns ok.

    Returns the updated prompt and active_hats list.
    The critic hat is activated for this call and deactivated immediately after.
    """
    critic_active = False
    try:
        active_hats = self._hat_engine.apply_hat(active_hats, "critic")
        critic_active = True
    except ValueError:
        # No critic hat registered — skip critique silently
        return prompt, active_hats

    critic_prompt = (
        f"{prompt}\n\n[CRITIC REVIEW REQUEST]\n"
        f"Review the result of '{tool_name}' above.\n"
        f"If the result is acceptable, call: {{\"tool\": \"critic_approve\", \"args\": {{}}}}\n"
        f"If you have concerns, describe them as plain text."
    )
    enriched = self._hat_engine.inject_hats(critic_prompt, active_hats)

    try:
        raw = await self._text_runner(enriched, system_msg, "critic")
    except Exception:
        logger.exception("Critic pass failed session=%s tool=%s", session_id, tool_name)
        raw = ""
    finally:
        if critic_active:
            active_hats = self._hat_engine.drop_hat(active_hats, "critic")

    parsed = _parse_tool_call(raw)
    if parsed is not _NO_TOOL and parsed.get("tool") == "critic_approve":
        # Approved — no change to prompt
        return prompt, active_hats

    # Critique injected — append as CRITIC note for next round
    critique = raw.strip()
    if critique:
        prompt = f"{prompt}\n\nCRITIC: {critique}"

    return prompt, active_hats
```

---

## Test: `tests/test_forge_critique.py`

```python
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from skillforge.types import MemorySnapshot, ToolResult


def _make_critique_forge(tool_response: ToolResult, llm_responses: list[str]):
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    call_idx = [0]

    async def _runner(prompt, system, label=""):
        idx = call_idx[0]
        call_idx[0] += 1
        return llm_responses[idx] if idx < len(llm_responses) else "Done."

    forge = Forge(
        base_system_prompt="test",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "reviewed_tool",
        AsyncMock(return_value=tool_response),
        critique_enabled=True,
    )
    return forge


@pytest.mark.asyncio
async def test_critic_approve_no_prompt_change():
    """
    When critic approves, the final reply is the LLM's post-tool response.
    The critic_approve JSON is NOT exposed to the user.
    """
    forge = _make_critique_forge(
        ToolResult(summary="output ready", status="ok"),
        llm_responses=[
            '{"tool": "reviewed_tool", "args": {}}',   # LLM calls tool
            '{"tool": "critic_approve", "args": {}}',  # critic approves
            "Here is your result.",                     # LLM final reply
        ],
    )
    result = await forge.run_turn(
        session_id="s1", user_message="Do the thing", context={}
    )
    assert result.reply == "Here is your result."


@pytest.mark.asyncio
async def test_critic_critique_injected_into_prompt():
    """
    When critic returns plain text, the critique appears in the prompt for
    the next iteration (LLM sees it and can refine).
    """
    critique_text = "The output is missing cost breakdown."
    forge = _make_critique_forge(
        ToolResult(summary="output ready", status="ok"),
        llm_responses=[
            '{"tool": "reviewed_tool", "args": {}}',   # LLM calls tool
            critique_text,                              # critic critique
            "Here is the revised result with cost.",   # LLM reply after seeing critique
        ],
    )
    result = await forge.run_turn(
        session_id="s1", user_message="Do the thing", context={}
    )
    assert "revised result" in result.reply


@pytest.mark.asyncio
async def test_no_critique_when_tool_not_critique_enabled():
    """
    Tools without critique_enabled=True should not trigger a critic pass.
    Exactly 2 LLM calls: tool call + final reply.
    """
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    call_count = [0]

    async def _runner(prompt, system, label=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return '{"tool": "plain_tool", "args": {}}'
        return "Done."

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "plain_tool",
        AsyncMock(return_value=ToolResult(summary="ok", status="ok")),
        critique_enabled=False,  # explicit
    )
    await forge.run_turn(session_id="s1", user_message="Go", context={})
    assert call_count[0] == 2  # tool call + final reply only


@pytest.mark.asyncio
async def test_blocked_result_skips_critique():
    """
    A critique_enabled tool that returns status='blocked' does NOT get critiqued.
    """
    import agent.hat_engine as hat_engine
    from skillforge import Forge

    call_count = [0]

    async def _runner(prompt, system, label=""):
        call_count[0] += 1
        if call_count[0] == 1:
            return '{"tool": "reviewed_tool", "args": {}}'
        return "Blocked."

    class _Mem:
        def assemble(self, **_kw):
            return MemorySnapshot(session_id="s1")
        def update(self, **_kw):
            return {}

    forge = Forge(
        base_system_prompt="t",
        hat_engine=hat_engine,
        memory=_Mem(),
        text_runner=_runner,
    )
    forge.register_tool(
        "reviewed_tool",
        AsyncMock(return_value=ToolResult(summary="blocked reason", status="blocked")),
        critique_enabled=True,
    )
    await forge.run_turn(session_id="s1", user_message="Go", context={})
    # Only 2 calls: tool + final reply. No critic call.
    assert call_count[0] == 2
```

---

## Acceptance Criteria

1. `python3.11 -m compileall skillforge/forge.py` exits 0
2. `pytest tests/test_forge_critique.py -v` — 4 passed
3. `pytest tests/test_forge.py -v` — 14 passed (no regressions)
4. `pytest tests/test_specialist_mode_routing.py -v` — no regressions
5. `grep "critique_enabled" skillforge/forge.py` — matches (flag is used)

---

## Do NOT Do

- Do not change `critique_enabled` in the registry — it already stores the flag
- Do not add a new `critic_approve` tool to the registry — it is handled inline
  as a special tool name in `_run_critique_pass()`
- Do not modify `agent/archie_loop.py` — all changes are inside `skillforge/`
- Do not loop the critic — one critique pass per tool call

---

## Commit Message

```
p3e: implement critique_enabled post-tool critic loop in Forge
```

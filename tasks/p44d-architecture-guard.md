# Task p44d: Architecture Guard — CLAUDE.md Rule + Regression Test

## Objective

Add a CLAUDE.md architectural constraint and an integration test that fail
loudly if the `archie_loop.py` bypass pattern is ever re-introduced.

**IMPORTANT:** Branch from main AFTER p44a–p44c are merged.

---

## Scope

**Touch:**
- `CLAUDE.md` — add architectural rule under Known Debt
- `tests/test_archie_forge_wiring.py` — new test file
- `agent/archie_loop.py` — rename to `agent/archie_session.py` (signals role)
- `agent/orchestrator_agent.py` and any import sites — update import

**Also touch:**
- `tests/test_archie_loop_invoke_tool.py` — delete the test case
  `test_prerouting_bom_uses_invoke_tool` which asserts the old bypass behaviour
  (forge.run_turn NOT called for BOM). This test now conflicts with p44c and
  must be removed before the new wiring test will pass.

**Do NOT touch:** `skillforge/`, hat files, other test files.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/archie_loop.py
grep "archie_session\|archie_loop" CLAUDE.md | wc -l
# must be zero — we are adding fresh
ls tests/test_archie_forge_wiring.py 2>/dev/null && echo EXISTS || echo MISSING
# must be MISSING
```

---

## Changes

### 1. `CLAUDE.md` — add rule under "Known Debt — Do Not Make Worse"

Add a new item:

```markdown
3. **`archie_session.py` is a thin session wrapper.** It must not contain
   routing logic, LLM calls outside `forge.run_turn()`, or tool dispatch.
   All orchestration belongs in `Forge`. All sequencing rules belong in the
   Archie system prompt. Any PR that adds routing logic to `archie_session.py`
   breaks the Forge reasoning loop silently — the p39–p43 expert reasoning
   will never fire for that request type.
```

### 2. Rename `agent/archie_loop.py` → `agent/archie_session.py`

```bash
git mv agent/archie_loop.py agent/archie_session.py
```

Update all import sites:
```bash
grep -rn "archie_loop\|from agent.archie_loop\|import archie_loop" \
  --include="*.py" . | grep -v ".pyc"
```
For each match, update the import to reference `archie_session`.

### 3. `tests/test_archie_forge_wiring.py` — new test

```python
"""
Architecture guard: assert forge.run_turn() is called for every
generation request. Fails if a bypass block is re-introduced in
archie_session.py.
"""
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from skillforge.types import TurnResult, ToolCallRecord, ToolResult


def _mock_turn_result(reply: str = "done") -> TurnResult:
    return TurnResult(
        reply=reply,
        tool_calls=[],
        events=[],
        artifacts={},
    )


@pytest.mark.parametrize("message", [
    "I need a BOM for a web app with 2 servers",
    "Generate a diagram for a 3-tier OCI architecture",
    "Run a WAF review on my current architecture",
    "Generate a Terraform plan for my diagram",
    "Write a POV document",
])
def test_forge_run_turn_called_for_generation_requests(message):
    """forge.run_turn() must be called for all generation messages."""
    from agent import archie_session

    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=_mock_turn_result())

    mock_store = MagicMock()
    mock_text_runner = MagicMock()

    with patch("agent.archie_session._get_forge", return_value=mock_forge), \
         patch("agent.archie_session.document_store") as mock_ds, \
         patch("agent.archie_session.context_store") as mock_cs, \
         patch("agent.archie_session.decision_context_builder") as mock_dcb:

        mock_ds.load_conversation_history.return_value = []
        mock_ds.load_conversation_summary.return_value = ""
        mock_cs.read_context = MagicMock(return_value={})
        mock_cs.get_pending_checkpoint.return_value = None
        mock_cs.get_pending_update.return_value = None
        mock_cs.build_context_summary.return_value = ""
        mock_dcb.build_decision_context.return_value = {}

        asyncio.run(archie_session.run_turn(
            customer_id="test",
            customer_name="Test User",
            user_message=message,
            store=mock_store,
            text_runner=mock_text_runner,
        ))

    mock_forge.run_turn.assert_called_once(), (
        f"forge.run_turn() was NOT called for message: '{message}'\n"
        "This means a bypass block in archie_session.py is routing "
        "this request directly to a tool, skipping Forge's reasoning loop."
    )
```

---

## Acceptance Criteria

1. Rename compiles cleanly:
   ```bash
   python3.11 -m compileall agent/archie_session.py
   ```

2. No remaining archie_loop imports:
   ```bash
   grep -rn "archie_loop" --include="*.py" . | grep -v ".pyc" | wc -l
   # must be 0
   ```

3. CLAUDE.md rule present:
   ```bash
   grep "archie_session.*thin session wrapper" CLAUDE.md | wc -l
   # must be 1
   ```

4. Architecture test passes:
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
p44d: architecture guard — CLAUDE.md rule, forge-wiring test, rename to archie_session.py
```

Branch: `claude/p44d` (from main, after p44a–p44c merged). Push when done.

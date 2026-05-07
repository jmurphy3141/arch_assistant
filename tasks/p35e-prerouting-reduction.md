# Task p35e: Pre-routing Reduction — First Increment (3 Rules → Skill Files)

## Goal

Move three specific routing rules from Python code in `archie_loop.py` into
`skills/intent_routing.md`. This is the first increment of a larger campaign;
it proves the pattern works and establishes the eval test harness.

Remove a Python routing block only after a corresponding eval test proves the
LLM follows the skill file instruction reliably. Do not attempt bulk removal.

---

## Prerequisite Check

```bash
ls skills/README.md            # p35b must be done
pytest tests/test_specialist_mode_routing.py -v --tb=short 2>&1 | tail -3
```

Both must pass. The 45 routing tests are the regression gate throughout.

---

## Scope

**Only modify:**
- `agent/archie_loop.py` — remove the three target Python blocks (see below)
- `agent/archie_wiring.py` — inject intent_routing skill into base prompt

**Only create:**
- `skills/intent_routing.md`
- `tests/test_prerouting_evals.py`

**Do NOT touch `skillforge/` in this task.**

---

## Target rules — pick these three first

These are the lowest-risk rules to move because they are soft guidance (the
LLM should prefer X) rather than hard blocks (the system refuses unless X).
Hard blocks (JEP lifecycle lock, topology preflight, checkpoint enforcement)
stay in Python.

### Rule 1: Architecture-chat-only detection
Currently: `_is_architecture_chat_only_request()` keyword list in Python.
Move to: A section in `skills/intent_routing.md` describing when to respond
conversationally vs. when to call a generation tool.

### Rule 2: Recall intent detection
Currently: `_is_recall_intent()` keyword list triggers a context summary reply.
Move to: A section in `skills/intent_routing.md` instructing the LLM to call
`get_summary` when the user asks what has been decided or documented.

### Rule 3: Note capture detection
Currently: `_is_note_capture_only_request()` triggers `save_notes` directly.
Move to: A section in `skills/intent_routing.md` instructing the LLM to call
`save_notes` when the user says things like "note that..." or "remember this".

---

## `skills/intent_routing.md`

```markdown
# Archie Intent Routing

This skill file guides how Archie decides what action to take for a user message.
It is prepended to the prompt at the start of each conversation turn.

---

## When to respond conversationally (no tool call)

Respond with plain text — do not call any tool — when the user is:
- Asking a general architecture question ("What is the difference between...?")
- Discussing trade-offs without asking for a deliverable
- Greeting or clarifying scope
- Asking what you can help with

Do NOT call a generation tool speculatively. Only call tools when the user
explicitly requests a deliverable (BOM, diagram, Terraform, POV, JEP, WAF)
or when producing one is clearly the right next step given the conversation.

---

## When to recall documented context

Call `get_summary` when the user asks about previously captured information:
- "What have we decided so far?"
- "What's in the architecture notes?"
- "Remind me what we agreed on"
- "What did we document?"

Do not reconstruct from memory — call `get_summary` and present the result.

---

## When to capture notes

Call `save_notes` when the user explicitly asks you to record something:
- "Note that..." / "Remember that..." / "Document this..."
- "Add to the notes..." / "Keep track of..."
- "Make a note that..."

Capture the note first, then confirm to the user that it was saved.
Do not combine note-saving with a generation tool in the same turn unless
the user explicitly requested both.
```

---

## Inject skill into base prompt

In `agent/archie_wiring.py`, in `build_forge()`, set the intent routing skill
as the `skill_guidance` for a lightweight routing meta-tool, OR — simpler —
prepend it to the `base_system_prompt` before passing to `Forge`:

```python
_INTENT_ROUTING_SKILL = Path(__file__).parent.parent / "skills" / "intent_routing.md"

def build_forge(..., base_system_prompt: str = "") -> Forge:
    routing_guidance = ""
    if _INTENT_ROUTING_SKILL.exists():
        routing_guidance = _INTENT_ROUTING_SKILL.read_text()

    full_prompt = (routing_guidance + "\n\n" + base_system_prompt).strip()

    forge = Forge(
        base_system_prompt=full_prompt,
        ...
    )
```

---

## Removing the Python blocks

For each of the three rules, after the eval tests pass:

1. Find the corresponding `if _is_architecture_chat_only_request(...)`,
   `if _is_recall_intent(...)`, `if _is_note_capture_only_request(...)` block
   in `run_turn()`
2. Delete it
3. Run `pytest tests/test_specialist_mode_routing.py -v` — must still pass 45/45
4. Run `pytest tests/test_prerouting_evals.py -v` — must pass

---

## Test: `tests/test_prerouting_evals.py`

These tests do NOT call the real LLM. They verify that the skill file content
is present in the assembled system prompt and that the Python routing blocks
have been removed.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_intent_routing_skill_in_system_prompt():
    """Intent routing guidance is present in Forge's assembled system prompt."""
    from agent.archie_wiring import build_forge

    forge = build_forge(
        store=None,
        customer_id="test",
        customer_name="Test",
        text_runner=AsyncMock(),
        base_system_prompt="You are Archie.",
    )
    system_msg = forge._get_system_msg()
    assert "conversationally" in system_msg or "no tool call" in system_msg.lower(), \
        "Intent routing skill not found in system prompt"


def test_recall_intent_not_hardcoded_in_run_turn():
    """_is_recall_intent should not be called from run_turn after migration."""
    import ast
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_recall_intent" not in source, \
        "_is_recall_intent is still called from run_turn — remove the Python block"


def test_note_capture_not_hardcoded_in_run_turn():
    """_is_note_capture_only_request should not be called from run_turn after migration."""
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_note_capture_only_request" not in source, \
        "_is_note_capture_only_request is still called from run_turn — remove the Python block"


def test_architecture_chat_not_hardcoded_in_run_turn():
    """_is_architecture_chat_only_request should not be called from run_turn."""
    import inspect
    import agent.archie_loop as archie_loop

    source = inspect.getsource(archie_loop.run_turn)
    assert "_is_architecture_chat_only_request" not in source, \
        "_is_architecture_chat_only_request still in run_turn — remove the Python block"


@pytest.mark.asyncio
async def test_run_turn_still_returns_reply_for_conversational_message(monkeypatch):
    """After removing Python blocks, run_turn still handles conversational messages."""
    import agent.archie_loop as archie_loop
    from skillforge.types import TurnResult

    fake_result = TurnResult(reply="Great question!", tool_calls=[], artifacts={}, history_length=1)
    mock_forge = MagicMock()
    mock_forge.run_turn = AsyncMock(return_value=fake_result)
    mock_forge.invoke_tool = AsyncMock()

    monkeypatch.setattr(archie_loop, "_get_forge", lambda **_kw: mock_forge)
    monkeypatch.setattr(archie_loop.document_store, "load_conversation_history", lambda *a: [])
    monkeypatch.setattr(archie_loop.document_store, "save_conversation_turns", lambda *a, **kw: None)
    monkeypatch.setattr(archie_loop.context_store, "read_context", lambda *a: {})

    with patch("agent.notifications.notify"):
        result = await archie_loop.run_turn(
            customer_id="c1",
            customer_name="Acme",
            user_message="What is the difference between OCI and AWS?",
            store=MagicMock(),
            text_runner=AsyncMock(return_value="done"),
        )

    assert result["reply"] == "Great question!"
```

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/archie_loop.py agent/archie_wiring.py` exits 0
2. `pytest tests/test_prerouting_evals.py -v` — 5 passed
3. `pytest tests/test_specialist_mode_routing.py -v` — 45 passed (no regression)
4. `pytest tests/test_archie_loop_cutover.py -v` — 2 passed
5. `ls skills/intent_routing.md` — exists
6. `grep "_is_recall_intent\|_is_note_capture_only_request\|_is_architecture_chat_only_request" agent/archie_loop.py | grep "run_turn\|forced_reply"` — no matches inside run_turn

---

## Do NOT Do

- Do not remove hard-block routing logic (JEP lifecycle, topology preflight,
  checkpoint enforcement, confirmation workflows) — those stay in Python
- Do not attempt more than 3 rules in this task
- Do not delete the Python helper functions (`_is_recall_intent` etc.) — only
  remove the call sites inside `run_turn()`. The helpers may be tested
  independently elsewhere

---

## Commit Message

```
p35e: move 3 routing rules to skills/intent_routing.md — first pre-routing reduction increment
```

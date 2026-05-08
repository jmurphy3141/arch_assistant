# Task p37e: POV Agent Interview Mode

## Goal

The POV agent immediately attempts to generate a full POV document even when
customer context is sparse. It should instead ask structured discovery questions
and only generate the document once enough context is gathered.

The fix is in `agent/pov_agent.py` — add a context-sufficiency check and return
`need_clarification` with targeted questions when context is too thin.

---

## Scope

**Only modify:** `agent/pov_agent.py`

**Do NOT touch:** `sub_agents/pov/`, `drawing_agent_server.py`, or any handler.

---

## Prerequisite Check

```bash
python3.11 -m compileall agent/pov_agent.py
grep "generate_pov\|need_clarification\|context_summary" agent/pov_agent.py | head -20
```

---

## What to implement

### 1. Add `_context_is_sufficient(context_summary, new_notes_text) -> bool`

```python
def _context_is_sufficient(context_summary: str, new_notes_text: str) -> bool:
    """
    Return True only when there is enough customer context to write a POV.
    Require at least one of: a non-trivial context summary, or substantive notes.
    """
    combined = (context_summary or "") + (new_notes_text or "")
    # Thin if combined text is under 150 chars or contains only boilerplate
    if len(combined.strip()) < 150:
        return False
    boilerplate_only = all(
        phrase in combined.lower()
        for phrase in ("no notes", "no context", "empty")
    )
    return not boilerplate_only
```

### 2. Add the structured question set

```python
POV_DISCOVERY_QUESTIONS = """
To write a high-quality POV I need to understand your customer's situation.
Please answer the following:

1. **Customer challenge**: What is the primary business problem or opportunity
   driving this engagement? (e.g. cost reduction, modernisation, compliance)

2. **Current state**: What does their current infrastructure or process look like?
   (On-premises, another cloud, hybrid?)

3. **Target workloads**: What specific workloads or applications are in scope?
   (e.g. Oracle DB, Kubernetes, analytics, AI/ML)

4. **Success criteria**: What does "success" look like in 12 months?
   (measurable outcomes: cost savings %, performance improvement, time to deploy)

5. **Timeline and urgency**: Is there a deadline, fiscal event, or executive
   milestone driving the timeline?

6. **Decision-makers**: Who are the key stakeholders? (CTO, CFO, Procurement?)

7. **Risks or objections**: What concerns has the customer raised about OCI or
   this engagement?

Answer as many as you can — I'll generate the POV from your responses.
""".strip()
```

### 3. Add early return in `generate_pov`

At the top of `generate_pov` (after reading context and notes), add:

```python
if not _context_is_sufficient(context_summary, new_notes_text):
    return {
        "status": "need_clarification",
        "questions": POV_DISCOVERY_QUESTIONS,
        "message": (
            "I need more information about the customer to write a POV. "
            "Please answer the discovery questions above."
        ),
    }
```

### 4. Handler response shaping

The `pov_generate` handler in `drawing_agent_server.py` already handles
`status == "need_clarification"` (same pattern as terraform). Verify this is
the case. If not, add:

```python
if result.get("status") == "need_clarification":
    return {"status": "need_clarification", "questions": result.get("questions", "")}
```

---

## Acceptance Criteria

1. `python3.11 -m compileall agent/pov_agent.py` exits 0
2. `grep "_context_is_sufficient" agent/pov_agent.py` — matches
3. `grep "POV_DISCOVERY_QUESTIONS" agent/pov_agent.py` — matches
4. `grep "need_clarification" agent/pov_agent.py` — matches
5. Unit test: call `generate_pov` with empty `context_summary` and empty
   `new_notes_text` — assert result has `status == "need_clarification"`
   and `"questions"` key with non-empty string.
6. Unit test: call `generate_pov` with a 200+ character `context_summary` —
   assert it does NOT return `need_clarification` (proceeds to generation).
7. `pytest tests/ -q --tb=short 2>&1 | tail -5` — same pass count as before

---

## Commit Message

```
p37e: POV agent interview mode — ask discovery questions when context is sparse
```

Branch: `claude/p37e` (from main after p37a merges). Push when done.

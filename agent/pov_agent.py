"""
agent/pov_agent.py
-------------------
Point of View (POV) document generator (Agent 4 in fleet).

Each run:
  1. Reads context/{customer_id}/context.json
  2. Identifies notes not yet incorporated by this agent
  3. Reads base POV: approved version if one exists, else latest LLM-generated
  4. Calls LLM with: context summary + new notes + feedback history + base POV
  5. Saves new versioned POV + prompt log
  6. Persists any new feedback entry
  7. Updates context file with this run's results

Storage
-------
  Reads:  context/{customer_id}/context.json
          notes/{customer_id}/* (new notes only, diffed against context)
          approved/{customer_id}/pov.md       (preferred base — SA ground truth)
          pov/{customer_id}/LATEST.md         (fallback base)
          pov/{customer_id}/feedback.json     (prior correction history)
  Writes: pov/{customer_id}/v{n}.md + LATEST.md + MANIFEST.json
          pov/{customer_id}/v{n}_prompt_log.json
          pov/{customer_id}/feedback.json     (if feedback provided)
          context/{customer_id}/context.json  (updated)
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from agent.context_store import (
    build_context_summary,
    get_new_notes,
    read_context,
    record_agent_run,
    write_context,
)
from agent.document_store import (
    append_feedback,
    get_best_base_doc,
    get_feedback_history,
    save_doc,
    save_prompt_log,
)
from agent.notifications import notify
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

AGENT_NAME = "pov"

POV_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud solutions architect writing a Point of View (POV) document. "
    "A POV is an internal Oracle document that casts a vision for customer success on OCI. "
    "It includes an internal visionary press release (future-state success story), "
    "a customer FAQ section, and internal Oracle strategy questions. "
    "Write in a confident, professional tone. Be specific about OCI services and capabilities. "
    "Reference the customer's industry and concrete challenges found in the notes. "
    "Output ONLY the document content in Markdown format. No meta-commentary, no preamble."
)

_PROMPT_TEMPLATE = """\
Write a Point of View (POV) document for Oracle Cloud Infrastructure (OCI).

Customer: {customer_name}

{context_summary}

{new_notes_section}

{feedback_section}

{previous_pov_section}

{instructions}

Generate a complete, professionally written POV in Markdown. Use this exact structure:

# {customer_name} — Oracle Cloud Point of View

## Internal Visionary Press Release

### Summary
[Write 2–3 paragraphs as a future-state press release (12–18 months from now).
Announce the customer's partnership with Oracle and describe what success looks like.
Reference: customer's industry, key challenges, OCI capabilities used, measurable outcomes.]

### Problem
- [Key business or technical challenge #1]
- [Key business or technical challenge #2]
- [Key business or technical challenge #3]

### Solution
[2–3 paragraphs describing how OCI addresses the challenges with specific services.]

### Oracle Quote
> "[Quote from a fictional Oracle GVP/VP.]"
> — [Name], [Title], Oracle

### Customer Quote
> "[CTO/CIO quote about technical outcomes.]"
> — [Name], [Title], {customer_name}

> "[CEO/COO quote about business impact.]"
> — [Name], [Title], {customer_name}

---

## External (Customer) Questions

**Q: What challenges are {customer_name} and Oracle addressing together?**
A: [2–3 sentences.]

**Q: What specific OCI solutions are being implemented?**
A: [3–4 OCI capabilities with brief descriptions.]

**Q: How does this benefit {customer_name}'s customers or operations?**
A: [2–3 sentences.]

**Q: Is {customer_name} moving its entire infrastructure to OCI?**
A: [Migration scope answer.]

**Q: What's next in the partnership?**
A: [Next milestones.]

---

## Internal (Oracle) Questions

**Q: What are {customer_name}'s primary technical requirements and regulatory constraints?**
A: [Specific requirements.]

**Q: What resources will {customer_name} allocate and will Oracle engineers be embedded?**
A: [Resourcing plan.]

**Q: What is {customer_name}'s migration timeline and scaling expectations?**
A: [Timeline and growth projections.]

**Q: What strategic role does {customer_name} envision for Oracle?**
A: [Partnership model.]

**Q: What are the key dependencies for this engagement to succeed?**
A: [Dependencies and risks.]
"""


def generate_pov(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    *,
    feedback: str = "",
) -> dict:
    """
    Generate or update a POV document.

    Args:
        customer_id:   Customer identifier — bucket key prefix.
        customer_name: Human-readable customer name.
        store:         ObjectStoreBase instance.
        text_runner:   callable(prompt: str, system_message: str) -> str.
        feedback:      Optional SA free-text corrections to incorporate.
                       Saved permanently and included in all future generations.

    Returns dict with keys:
        version (int), key (str), latest_key (str), content (str), context (dict)
    """
    # ── Read context + diff new notes ─────────────────────────────────────────
    context = read_context(store, customer_id, customer_name)
    if customer_name and not context.get("customer_name"):
        context["customer_name"] = customer_name

    new_note_keys, new_notes_text = get_new_notes(store, context, AGENT_NAME)
    context_summary = build_context_summary(context)

    # ── Base document: approved first, then latest LLM-generated ─────────────
    base_doc = get_best_base_doc(store, "pov", customer_id)

    # ── Feedback history (all prior + current) ────────────────────────────────
    prior_feedback = get_feedback_history(store, "pov", customer_id)
    all_feedback_entries = prior_feedback + (
        [{"feedback": feedback}] if feedback.strip() else []
    )

    # ── Build prompt sections ─────────────────────────────────────────────────
    context_block = f"{context_summary}\n" if context_summary else ""

    if new_notes_text:
        new_notes_section = (
            "New meeting notes to incorporate (not yet in previous version):\n"
            f"{new_notes_text[:4000]}"
        )
    elif not base_doc:
        new_notes_section = "(No meeting notes uploaded yet — generate a skeleton POV.)"
    else:
        new_notes_section = "(No new notes since last run — refine the existing POV if needed.)"

    if all_feedback_entries:
        feedback_lines = "\n".join(
            f"  • {e['feedback']}" for e in all_feedback_entries if e.get("feedback")
        )
        feedback_section = (
            "SA correction history (apply ALL of these — do not repeat previous mistakes):\n"
            f"{feedback_lines}"
        )
    else:
        feedback_section = ""

    if base_doc:
        previous_pov_section = (
            "Previous POV version (use as base; update and improve — do not repeat verbatim):\n"
            "```\n"
            + base_doc[:3000]
            + "\n```"
        )
        instructions = (
            "Update the POV: incorporate the new notes, apply ALL corrections above, "
            "keep strong sections, improve weak ones."
        )
    else:
        previous_pov_section = ""
        instructions = "This is the first POV for this customer. Write a complete draft."

    prompt = _PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        context_summary=context_block,
        new_notes_section=new_notes_section,
        feedback_section=feedback_section,
        previous_pov_section=previous_pov_section,
        instructions=instructions,
    )

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info("Generating POV: customer_id=%s new_notes=%d feedback=%r",
                customer_id, len(new_note_keys), bool(feedback.strip()))
    content = text_runner(prompt, POV_SYSTEM_MESSAGE)

    # ── Persist doc ───────────────────────────────────────────────────────────
    result = save_doc(store, "pov", customer_id, content, {"customer_name": customer_name})

    # ── Save prompt log ───────────────────────────────────────────────────────
    save_prompt_log(store, "pov", customer_id, result["version"], {
        "system_message":       POV_SYSTEM_MESSAGE,
        "prompt":               prompt,
        "response_length_chars": len(content),
        "new_note_keys":        new_note_keys,
        "feedback_provided":    feedback.strip(),
    })

    # ── Persist feedback if provided ──────────────────────────────────────────
    if feedback.strip():
        append_feedback(store, "pov", customer_id, feedback.strip(), result["version"])

    # ── Update + write context ────────────────────────────────────────────────
    first_line = next(
        (ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")),
        "",
    )
    context = record_agent_run(
        context,
        AGENT_NAME,
        new_note_keys,
        {
            "version": result["version"],
            "key":     result["key"],
            "summary": first_line[:120],
        },
    )
    write_context(store, customer_id, context)

    result["content"] = content
    result["context"] = context
    logger.info("POV saved: version=%d key=%s", result["version"], result["key"])

    notify("pov_generated", customer_id,
           f"POV v{result['version']} generated for {customer_name}")
    return result

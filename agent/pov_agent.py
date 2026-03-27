"""
agent/pov_agent.py
-------------------
Point of View (POV) document generator (Agent 4 in fleet).

A POV is an internal Oracle document casting a vision for customer success on OCI.
It is updated incrementally as meeting notes are added — each call reads all existing
notes plus the previous version and produces an updated draft.

Document structure
------------------
1. Internal Visionary Press Release
   - Summary (future-state success story, 12–18 months out)
   - Problem (key challenges)
   - Solution (OCI capabilities used)
   - Oracle Quote
   - Customer Quote (two executives)

2. External (Customer) Q&A — 5 standard questions

3. Internal (Oracle) Q&A — 4–5 strategy/discovery questions

Storage
-------
  Input:  notes/{customer_id}/*          (all meeting notes, read by document_store)
          pov/{customer_id}/LATEST.md    (previous version, if any)
  Output: pov/{customer_id}/v{n}.md
          pov/{customer_id}/LATEST.md
          pov/{customer_id}/MANIFEST.json
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from agent.document_store import get_all_notes_text, get_latest_doc, save_doc
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

# System message injected before every POV generation call
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

Meeting Notes:
{notes_text}

{previous_pov_section}

Generate a complete, professionally written POV in Markdown. Use this exact structure:

# {customer_name} — Oracle Cloud Point of View

## Internal Visionary Press Release

### Summary
[Write 2–3 paragraphs as a future-state press release (12–18 months from now).
Announce the customer's partnership with Oracle and describe what success looks like.
Reference: customer's industry, the key challenges they face, OCI capabilities used,
and measurable business outcomes achieved.]

### Problem
- [Key business or technical challenge #1]
- [Key business or technical challenge #2]
- [Key business or technical challenge #3]
[2–4 bullet points total, drawn directly from the meeting notes.]

### Solution
[2–3 paragraphs describing how OCI addresses the customer's challenges.
Name specific OCI services (e.g., OKE, Autonomous DB, OCI GenAI, Bare Metal GPU,
Object Storage, etc.). Be concrete and technical.]

### Oracle Quote
> "[Write a quote from a fictional Oracle GVP or VP congratulating the partnership
> and highlighting the business impact achieved.]"
> — [Name], [Title], Oracle

### Customer Quote
> "[Write a CTO or CIO quote about the technical outcomes and why OCI was the right choice.]"
> — [Name], [Title], {customer_name}

> "[Write a CEO or COO quote about the business impact and strategic value of the partnership.]"
> — [Name], [Title], {customer_name}

---

## External (Customer) Questions

**Q: What challenges are {customer_name} and Oracle addressing together?**
A: [2–3 sentences describing the business and technical challenges and why OCI is the answer.]

**Q: What specific OCI solutions are being implemented?**
A: [List 3–4 OCI capabilities with a brief description of each. Use bullet points.]

**Q: How does this benefit {customer_name}'s customers or operations?**
A: [2–3 sentences on the customer/end-user benefit: reliability, speed, compliance, etc.]

**Q: Is {customer_name} moving its entire infrastructure to OCI?**
A: [Honest answer about migration scope — full migration, hybrid, or workload-specific.]

**Q: What's next in the partnership?**
A: [1–2 sentences about upcoming milestones, events, or expansion plans.]

---

## Internal (Oracle) Questions

**Q: What are {customer_name}'s primary technical requirements and regulatory constraints?**
A: [Specific tech requirements, compliance frameworks, audit cadence, data residency needs.]

**Q: What dedicated resources will {customer_name} allocate, and will Oracle engineers be embedded?**
A: [Team composition, Oracle embedding expectations, resourcing plan.]

**Q: What is {customer_name}'s migration timeline and scaling expectations over 2–3 years?**
A: [Phased vs. big-bang, growth projections, capacity planning.]

**Q: What strategic role does {customer_name} envision for Oracle — infrastructure provider, strategic partner, or co-innovator?**
A: [Partnership model: transactional, co-development, innovation lab, etc.]

**Q: What are the key commercial and technical dependencies for this engagement to succeed?**
A: [Integration requirements, data dependencies, executive sponsorship, procurement timelines.]
"""


def generate_pov(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
) -> dict:
    """
    Generate or update a POV document for a customer.

    Reads all notes from the bucket, reads the previous version (if any),
    calls the LLM, and saves the new version atomically.

    Args:
        customer_id:   Customer identifier — used as the bucket key prefix.
        customer_name: Human-readable customer name — used in the document.
        store:         ObjectStoreBase instance (real OCI or InMemory for tests).
        text_runner:   callable(prompt: str, system_message: str) -> str.
                       Returns raw LLM text output (not parsed JSON).

    Returns:
        dict with keys:
            version (int), key (str), latest_key (str), content (str)
    """
    # ── Gather context ────────────────────────────────────────────────────────
    notes_text = get_all_notes_text(store, customer_id)
    if not notes_text:
        logger.warning("No notes found for customer=%s; generating skeleton POV", customer_id)
        notes_text = "(No meeting notes available — generate a skeleton POV based on customer name only.)"

    previous_pov = get_latest_doc(store, "pov", customer_id)
    if previous_pov:
        previous_pov_section = (
            "Previous POV version (use for context and continuity; "
            "update and improve based on new notes — do not simply repeat it):\n"
            "```\n"
            + previous_pov[:3000]
            + "\n```\n"
        )
    else:
        previous_pov_section = ""

    # ── Build prompt ──────────────────────────────────────────────────────────
    prompt = _PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        notes_text=notes_text[:5000],
        previous_pov_section=previous_pov_section,
    )

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info("Generating POV: customer_id=%s customer_name=%r", customer_id, customer_name)
    content = text_runner(prompt, POV_SYSTEM_MESSAGE)

    # ── Persist ───────────────────────────────────────────────────────────────
    result = save_doc(store, "pov", customer_id, content, {"customer_name": customer_name})
    result["content"] = content
    logger.info("POV saved: version=%d key=%s", result["version"], result["key"])
    return result

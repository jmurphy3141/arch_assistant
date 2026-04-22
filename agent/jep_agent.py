"""
agent/jep_agent.py
-------------------
Joint Execution Plan (JEP) document generator (Agent 5 in fleet).

Each run:
  1. Reads context/{customer_id}/context.json
  2. Identifies notes not yet incorporated by this agent
  3. Reads base JEP: approved version if one exists, else latest LLM-generated
  4. Calls LLM with: context summary + new notes + feedback history + Q&A answers
     + POC BOM + POC diagram ref + base JEP
  5. Saves new versioned JEP + prompt log
  6. Persists any new feedback entry
  7. Updates context file with this run's results

Kickoff flow (call kickoff_jep before generate_jep):
  1. LLM scans all notes for POC signals
  2. Returns structured Q&A (POC duration, workloads, success criteria, scope, etc.)
  3. Q&A saved to jep/{customer_id}/poc_questions.json
  4. SA answers questions in UI; answers saved back to same file
  5. SA triggers generate_jep once ready

Storage
-------
  Reads:  context/{customer_id}/context.json
          notes/{customer_id}/* (new notes only, diffed against context)
          approved/{customer_id}/jep.md        (preferred base — SA ground truth)
          jep/{customer_id}/LATEST.md          (fallback base)
          jep/{customer_id}/feedback.json      (prior correction history)
          jep/{customer_id}/poc_questions.json (kickoff Q&A answers)
          agent3/{customer_id}/poc/LATEST.json (POC diagram, if generated)
  Writes: jep/{customer_id}/v{n}.md + LATEST.md + MANIFEST.json
          jep/{customer_id}/v{n}_prompt_log.json
          jep/{customer_id}/feedback.json      (if feedback provided)
          jep/{customer_id}/poc_questions.json (kickoff output)
          context/{customer_id}/context.json   (updated)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from agent.bom_stub import bom_to_markdown, generate_stub_bom
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
    get_jep_questions,
    save_doc,
    save_jep_questions,
    save_prompt_log,
)
from agent.notifications import notify
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

AGENT_NAME = "jep"

# ── Kickoff LLM config ────────────────────────────────────────────────────────

_KICKOFF_SYSTEM = (
    "You are an Oracle Cloud solutions architect preparing a POC kickoff. "
    "Your job is to scan meeting notes for POC signals and identify what information "
    "is already known and what still needs to be confirmed with the customer. "
    "Operating contract: extract known facts first, then ask only missing high-value execution questions. "
    "Respond ONLY with a valid JSON object — no prose, no markdown fences."
)

_KICKOFF_PROMPT = """\
Scan the following meeting notes for signals about an upcoming OCI Proof of Concept (POC).

Customer: {customer_name}

Meeting notes:
{notes_text}

Extract what is already known and generate clarifying questions for what is missing.

Return a JSON object with this exact structure:
{{
  "extracted": {{
    "duration": "<duration string or null>",
    "workloads": "<description of workloads/GPU types or null>",
    "success_criteria": "<criteria or null>",
    "scope_in": "<what is in scope or null>",
    "scope_out": "<what is excluded or null>",
    "hardware_provided_by_customer": "<true/false/null>",
    "location": "<remote/on-site/hybrid or null>",
    "data_transfer": "<description or null>"
  }},
  "questions": [
    {{
      "id": "duration",
      "question": "What is the planned duration of the POC?",
      "hint": "e.g. 2 weeks, 14 days",
      "known_value": "<value from notes or null>"
    }},
    {{
      "id": "workloads",
      "question": "Which workloads and GPU types will be tested?",
      "hint": "e.g. LLM training on H100, inference on A100",
      "known_value": "<value from notes or null>"
    }},
    {{
      "id": "success_criteria",
      "question": "What are the pass/fail success criteria?",
      "hint": "e.g. NCCL all-reduce > 200 GB/s, provisioning < 10 min",
      "known_value": "<value from notes or null>"
    }},
    {{
      "id": "scope_in",
      "question": "Which OCI services and workloads are in scope?",
      "hint": "List specific services to be tested",
      "known_value": "<value from notes or null>"
    }},
    {{
      "id": "scope_out",
      "question": "Are any services or workloads explicitly out of scope?",
      "hint": "e.g. DR setup, data migration",
      "known_value": "<value from notes or null>"
    }},
    {{
      "id": "hardware",
      "question": "Is the customer providing any hardware or is it all OCI-hosted?",
      "hint": "e.g. all OCI, customer brings on-prem GPUs for comparison",
      "known_value": "<value from notes or null>"
    }}
  ]
}}

Only include questions where the answer is not clearly stated in the notes.
If the notes clearly answer a question, set known_value to the extracted value.
"""

# ── JEP generation LLM config ─────────────────────────────────────────────────

JEP_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud solutions architect writing a Joint Execution Plan (JEP) for a POC. "
    "A JEP defines POC goals, success criteria, scope, BOM, participants, deliverables, and logistics. "
    "Write in precise, professional language suitable for an Oracle–customer engagement document. "
    "Be specific about hardware specs, software versions, and measurable success criteria. "
    "Use Markdown tables for hardware specs, software specs, participants, and BOM. "
    "Fill in values from the meeting notes. Use [TBD] where information is not available. "
    "Operating contract: make scope, milestones, ownership, risks, and success criteria explicit and actionable. "
    "Output ONLY the document content in Markdown format. No meta-commentary, no preamble."
)

_PROMPT_TEMPLATE = """\
Write a Joint Execution Plan (JEP) for an Oracle Cloud Infrastructure (OCI) POC.

Customer: {customer_name}

{context_summary}

{new_notes_section}

{feedback_section}

{qa_section}

{previous_jep_section}

Bill of Materials (pre-generated from notes):
{bom_md}

Diagram reference:
{diagram_ref}

Generate a complete, professionally written JEP in Markdown. Use this exact structure:

# AI Infrastructure on OCI — {customer_name}
*Confidential — Oracle Restricted*

---

## Overview
[2–3 paragraphs describing: (1) enterprise demand for GPU/compute resources and why it is growing;
(2) the dual challenges organisations face (power density, operational expertise);
(3) how OCI addresses these challenges.
Include 2–3 bullet points for the primary driving factors.]

## High Level Scope and Approach
[1–2 paragraphs: what the customer will test, the primary focus areas.]

Key objectives include:
- [objective drawn from notes]
- [objective drawn from notes]
- [add further objectives as warranted]

### Hardware Specs
| Component | Specification |
|-----------|---------------|
[Fill from notes: GPU model, GPU memory, CPU OCPU count, RAM, local NVMe storage.
Use [TBD] for missing values.]

### Software Specs
| Component | Specification |
|-----------|---------------|
[Fill from notes: host OS, CUDA version, container runtime, Kubernetes version,
ML framework (PyTorch/TensorFlow), workload description.
Use [TBD] for missing values.]

## Future State Architecture
{diagram_ref}

[1 paragraph describing the target OCI architecture for the POC based on the notes and BOM.]

## POC Plan
[Describe the POC timeline, approach, and phases.
Default to a 2-week duration unless the notes or Q&A specify otherwise.
Include pre-POC setup (allow-lists, image pre-pull) and post-POC activities (results documentation).]

## Proof of Concept Test Cases

| # | Test Case | Description | Pass Criteria |
|---|-----------|-------------|---------------|
[Infer 4–6 test cases from the notes and scope.
Include performance benchmarks (NCCL throughput, GPU utilisation),
provisioning speed, networking (NVLink / RDMA), storage I/O, and Kubernetes operations.
Fill Pass Criteria with measurable thresholds where possible.]

## Success Criteria
[4–6 bullet points with measurable success criteria inferred from the notes.
Examples: provisioning time < X minutes, NCCL all-reduce bandwidth > X GB/s, etc.]
- [criterion]
- [criterion]

## Bill of Materials

{bom_md}

## POC Participants

### Oracle Team Members
| Name | Role |
|------|------|
| [TBD] | Account Executive |
| [TBD] | Solutions Architect |
| [TBD] | Cloud Engineer |

### {customer_name} Team Members
| Name | Role |
|------|------|
[Extract names and roles from notes. Use [TBD] rows if not mentioned.]

## Deliverables
- Documentation of POC test results and performance benchmarks
- Architecture diagram (draw.io) — see Future State Architecture section
- Final POC report with pass/fail assessment against success criteria
[Add further deliverables mentioned in notes.]

## Logistics

### Location
[Describe remote/on-site arrangement from notes.
Default if not specified: Oracle resources working remotely; customer team working on-site or remotely.]

### Data Transfer
[Describe data transfer approach from notes.
Default if not specified: Data transferred to Oracle Cloud into OCI Object Storage.]

### Communication
[Describe communication plan from notes — daily stand-ups, shared Slack/Teams workspace,
weekly steering calls, etc.]

### Data Cleansing
[Describe any data masking or cleansing requirements from notes.
Default if not specified: Customer will remove or mask any sensitive data used in the POC.
Oracle has no data cleansing effort.]

### Timing
**POC Duration**: {duration}

[Describe start/end dates if mentioned in notes, otherwise leave as [TBD].]

---
*Oracle Corporation | 2300 Oracle Way, Austin, TX 78741*
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_poc_diagram(
    store: ObjectStoreBase,
    customer_id: str,
    context: dict,
    persistence_prefix: str = "agent3",
) -> tuple[Optional[str], Optional[str]]:
    """
    Look for a POC-specific diagram in the bucket.

    Search order:
      1. agent3/{customer_id}/poc/LATEST.json  (POC diagram)
      2. context["agents"]["diagram"]["diagram_key"] if path contains /poc/
      3. None

    Returns (diagram_key, diagram_url) — either may be None.
    """
    poc_latest = f"{persistence_prefix}/{customer_id}/poc/LATEST.json"
    try:
        raw = store.get(poc_latest)
        data = json.loads(raw)
        key = data.get("diagram_key") or data.get("key")
        url = data.get("url") or data.get("diagram_url")
        if key:
            logger.debug("POC diagram found at %s", poc_latest)
            return key, url
    except (KeyError, json.JSONDecodeError):
        pass

    # Fall back to context if the diagram key references the poc/ path
    diagram_ctx = context.get("agents", {}).get("diagram", {})
    ctx_key = diagram_ctx.get("diagram_key", "")
    if ctx_key and "/poc/" in ctx_key:
        return ctx_key, diagram_ctx.get("diagram_url")

    return None, None


def _infer_duration(notes_text: str, qa_answers: Optional[dict] = None) -> str:
    """
    Extract POC duration.  Q&A answers take priority over free-text scan.
    Returns a human-readable string.
    """
    # Q&A answer wins if present
    if qa_answers:
        dur = qa_answers.get("duration", "")
        if dur and str(dur).strip():
            return str(dur).strip()

    lower = notes_text.lower()
    if "14-day" in lower or "14 day" in lower:
        return "14 days"
    m = re.search(r"(\d+)[- ]day", lower)
    if m:
        return f"{m.group(1)} days"
    m = re.search(r"(\d+)[- ]week", lower)
    if m:
        weeks = int(m.group(1))
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    return "2 weeks (default — confirm with customer)"


# ── Kickoff ───────────────────────────────────────────────────────────────────

def kickoff_jep(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
) -> dict:
    """
    Scan all meeting notes for POC signals and generate clarifying questions.

    Saves results to jep/{customer_id}/poc_questions.json.

    Returns dict with keys:
        questions (list[dict])  — list of Q&A dicts with id, question, hint, known_value
        extracted (dict)        — values extracted directly from notes
        questions_key (str)     — bucket key where questions were saved
    """
    from agent.document_store import get_all_notes_text

    notes_text = get_all_notes_text(store, customer_id)
    if not notes_text:
        notes_text = "(No meeting notes uploaded yet.)"

    prompt = _KICKOFF_PROMPT.format(
        customer_name=customer_name,
        notes_text=notes_text[:5000],
    )

    logger.info("Running JEP kickoff for customer_id=%s", customer_id)
    raw = text_runner(prompt, _KICKOFF_SYSTEM)

    # Parse JSON — strip markdown fences if the LLM wrapped it anyway
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-z]*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean.strip())

    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("Kickoff LLM response was not valid JSON; storing raw text")
        data = {
            "extracted": {},
            "questions": [
                {
                    "id": "raw_response",
                    "question": "The kickoff agent returned unstructured text — please review.",
                    "hint": raw[:500],
                    "known_value": None,
                }
            ],
        }

    questions = data.get("questions", [])
    extracted = data.get("extracted", {})

    questions_key = save_jep_questions(store, customer_id, questions)

    notify("jep_kickoff", customer_id,
           f"JEP kickoff complete for {customer_name} — {len(questions)} questions generated")

    logger.info("JEP kickoff saved: customer_id=%s questions=%d key=%s",
                customer_id, len(questions), questions_key)

    return {
        "questions":     questions,
        "extracted":     extracted,
        "questions_key": questions_key,
    }


# ── JEP generation ────────────────────────────────────────────────────────────

def generate_jep(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    *,
    feedback: str = "",
    diagram_key: Optional[str] = None,
    diagram_url: Optional[str] = None,
    persistence_prefix: str = "agent3",
) -> dict:
    """
    Generate or update a JEP document.

    Args:
        customer_id:         Customer identifier — bucket key prefix.
        customer_name:       Human-readable customer name.
        store:               ObjectStoreBase instance.
        text_runner:         callable(prompt: str, system_message: str) -> str.
        feedback:            Optional SA free-text corrections to incorporate.
                             Saved permanently and included in all future generations.
        diagram_key:         Explicit OCI bucket key for the POC diagram (optional).
                             If omitted, looks for agent3/{customer_id}/poc/LATEST.json.
        diagram_url:         Download URL for the diagram (optional).
        persistence_prefix:  Bucket prefix used by Agent 3 (default "agent3").

    Returns dict with keys:
        version (int), key (str), latest_key (str), content (str), bom (dict), context (dict)
    """
    # ── Read context + diff new notes ─────────────────────────────────────────
    context = read_context(store, customer_id, customer_name)
    if customer_name and not context.get("customer_name"):
        context["customer_name"] = customer_name

    new_note_keys, new_notes_text = get_new_notes(store, context, AGENT_NAME)
    context_summary = build_context_summary(context)

    # ── Base document: approved first, then latest LLM-generated ─────────────
    base_doc = get_best_base_doc(store, "jep", customer_id)

    # ── Feedback history (all prior + current) ────────────────────────────────
    prior_feedback = get_feedback_history(store, "jep", customer_id)
    all_feedback_entries = prior_feedback + (
        [{"feedback": feedback}] if feedback.strip() else []
    )

    # ── Kickoff Q&A answers ───────────────────────────────────────────────────
    qa_data = get_jep_questions(store, customer_id)
    qa_answers = qa_data.get("answers", {})
    qa_questions = qa_data.get("questions", [])

    # ── POC diagram reference ─────────────────────────────────────────────────
    if not diagram_key:
        diagram_key, diagram_url = _find_poc_diagram(
            store, customer_id, context, persistence_prefix
        )

    if diagram_key:
        diagram_ref = (
            f"*Architecture diagram generated by Agent 3 (POC).*  \n"
            f"Object Storage key: `{diagram_key}`"
            + (f"  \nDownload: {diagram_url}" if diagram_url else "")
        )
    else:
        diagram_ref = (
            "*Architecture diagram: [TBD — run the Architecture Diagram agent "
            "(Agent 3) with POC BOM to generate a draw.io diagram for this POC.]*"
        )

    # ── Build prompt sections ─────────────────────────────────────────────────
    context_block = f"{context_summary}\n" if context_summary else ""

    if new_notes_text:
        new_notes_section = (
            "New meeting notes to incorporate (not yet in previous version):\n"
            f"{new_notes_text[:4000]}"
        )
    elif not base_doc:
        new_notes_section = "(No meeting notes uploaded yet — generate a skeleton JEP.)"
    else:
        new_notes_section = "(No new notes since last run — refine the existing JEP if needed.)"

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

    if qa_questions or qa_answers:
        qa_lines: list[str] = []
        for q in qa_questions:
            qid = q.get("id", "")
            answer = qa_answers.get(qid) or q.get("known_value")
            if answer:
                qa_lines.append(f"  Q: {q['question']}\n  A: {answer}")
        if qa_lines:
            qa_section = (
                "POC kickoff Q&A (SA-confirmed answers — use these in preference to notes):\n"
                + "\n".join(qa_lines)
            )
        else:
            qa_section = ""
    else:
        qa_section = ""

    if base_doc:
        previous_jep_section = (
            "Previous JEP version (use as base; update and improve — do not repeat verbatim):\n"
            "```\n"
            + base_doc[:2500]
            + "\n```"
        )
        instructions = (
            "Update the JEP: incorporate the new notes, apply ALL corrections above, "
            "keep strong sections, improve weak ones."
        )
    else:
        previous_jep_section = ""
        instructions = "This is the first JEP for this customer. Write a complete draft."

    # ── Stub BOM from new notes (or placeholder text if first run) ────────────
    bom_text_for_stub = new_notes_text if new_notes_text else new_notes_section
    logger.info("Generating stub BOM for customer=%s", customer_id)
    bom = generate_stub_bom(bom_text_for_stub, text_runner, customer_name=customer_name)
    bom_md = bom_to_markdown(bom)

    # ── Duration ──────────────────────────────────────────────────────────────
    duration = _infer_duration(new_notes_text or bom_text_for_stub, qa_answers)

    # ── Build prompt ──────────────────────────────────────────────────────────
    prompt = _PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        context_summary=context_block,
        new_notes_section=new_notes_section,
        feedback_section=feedback_section,
        qa_section=qa_section,
        previous_jep_section=previous_jep_section,
        bom_md=bom_md,
        diagram_ref=diagram_ref,
        duration=duration,
    )

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info("Generating JEP: customer_id=%s new_notes=%d feedback=%r",
                customer_id, len(new_note_keys), bool(feedback.strip()))
    content = text_runner(prompt, JEP_SYSTEM_MESSAGE)

    # ── Persist doc ───────────────────────────────────────────────────────────
    result = save_doc(
        store, "jep", customer_id, content,
        {
            "customer_name": customer_name,
            "bom_source":    bom.get("source", "stub"),
            "duration":      duration,
        },
    )

    # ── Save prompt log ───────────────────────────────────────────────────────
    save_prompt_log(store, "jep", customer_id, result["version"], {
        "system_message":        JEP_SYSTEM_MESSAGE,
        "prompt":                prompt,
        "response_length_chars": len(content),
        "new_note_keys":         new_note_keys,
        "feedback_provided":     feedback.strip(),
        "diagram_key":           diagram_key,
        "qa_answers":            qa_answers,
    })

    # ── Persist feedback if provided ──────────────────────────────────────────
    if feedback.strip():
        append_feedback(store, "jep", customer_id, feedback.strip(), result["version"])

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
            "version":      result["version"],
            "key":          result["key"],
            "duration_days": bom.get("duration_days", 14),
            "bom_source":   bom.get("source", "stub"),
            "summary":      first_line[:120],
        },
    )
    write_context(store, customer_id, context)

    result["content"] = content
    result["bom"]     = bom
    result["context"] = context
    if diagram_key:
        result["diagram_key"] = diagram_key

    logger.info("JEP saved: version=%d key=%s", result["version"], result["key"])

    notify("jep_generated", customer_id,
           f"JEP v{result['version']} generated for {customer_name}")
    return result

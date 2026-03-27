"""
agent/jep_agent.py
-------------------
Joint Execution Plan (JEP) document generator (Agent 5 in fleet).

A JEP defines POC goals, success criteria, BOM, participants, deliverables,
and logistics.  It is created on demand (manual trigger) and updated when
new notes are added.

Orchestration
-------------
The JEP agent calls two sub-agents:

1. agent/bom_stub.py  — extracts a BOM from meeting notes via LLM.
   (Replace with A2A call to Agent 2 when it is available.)

2. Agent 3 (this project, drawing_agent_server.py) — referenced via the
   existing diagram in the bucket at agent3/{customer_id}/LATEST.json.
   The JEP agent does NOT re-generate the diagram; it references the latest
   diagram key already in the bucket.  The diagram is generated separately
   via the /upload-bom or /generate endpoints.

Storage
-------
  Input:  notes/{customer_id}/*                  (all meeting notes)
          jep/{customer_id}/LATEST.md             (previous version, if any)
          agent3/{customer_id}/*/LATEST.json      (latest diagram, if generated)
  Output: jep/{customer_id}/v{n}.md
          jep/{customer_id}/LATEST.md
          jep/{customer_id}/MANIFEST.json
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, Optional

from agent.bom_stub import generate_stub_bom, bom_to_markdown
from agent.document_store import get_all_notes_text, get_latest_doc, save_doc
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

JEP_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud solutions architect writing a Joint Execution Plan (JEP) for a POC. "
    "A JEP defines POC goals, success criteria, scope, BOM, participants, deliverables, and logistics. "
    "Write in precise, professional language suitable for an Oracle–customer engagement document. "
    "Be specific about hardware specs, software versions, and measurable success criteria. "
    "Use Markdown tables for hardware specs, software specs, participants, and BOM. "
    "Fill in values from the meeting notes. Use [TBD] where information is not available. "
    "Output ONLY the document content in Markdown format. No meta-commentary, no preamble."
)

_PROMPT_TEMPLATE = """\
Write a Joint Execution Plan (JEP) for an Oracle Cloud Infrastructure (OCI) POC.

Customer: {customer_name}

Meeting Notes:
{notes_text}

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
Default to a 2-week duration unless the notes specify otherwise.
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


def _infer_duration(notes_text: str) -> str:
    """Extract POC duration from notes text. Returns a human-readable string."""
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


def generate_jep(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    *,
    diagram_key: Optional[str] = None,
    diagram_url: Optional[str] = None,
    persistence_prefix: str = "agent3",
) -> dict:
    """
    Generate or update a JEP document for a customer.

    Reads all notes + previous JEP version (if any), calls the stub BOM
    generator, references the latest diagram from the bucket, then calls
    the LLM to draft the full JEP.

    Args:
        customer_id:         Customer identifier — bucket key prefix.
        customer_name:       Human-readable customer name.
        store:               ObjectStoreBase instance.
        text_runner:         callable(prompt: str, system_message: str) -> str.
        diagram_key:         Explicit OCI bucket key for the diagram (optional).
                             If omitted, the agent looks for the latest diagram
                             under {persistence_prefix}/{customer_id}/*/LATEST.json.
        diagram_url:         Download URL for the diagram (optional).
        persistence_prefix:  Bucket prefix used by Agent 3 (default "agent3").

    Returns:
        dict with keys:
            version (int), key (str), latest_key (str), content (str), bom (dict)
    """
    # ── Gather notes ──────────────────────────────────────────────────────────
    notes_text = get_all_notes_text(store, customer_id)
    if not notes_text:
        logger.warning("No notes found for customer=%s; generating skeleton JEP", customer_id)
        notes_text = "(No meeting notes available — generate a skeleton JEP based on customer name only.)"

    # ── Previous JEP version ──────────────────────────────────────────────────
    previous_jep = get_latest_doc(store, "jep", customer_id)
    if previous_jep:
        previous_jep_section = (
            "Previous JEP version (use for continuity; update based on new notes):\n"
            "```\n"
            + previous_jep[:2000]
            + "\n```\n"
        )
    else:
        previous_jep_section = ""

    # ── Stub BOM ──────────────────────────────────────────────────────────────
    logger.info("Generating stub BOM for customer=%s", customer_id)
    bom = generate_stub_bom(notes_text, text_runner, customer_name=customer_name)
    bom_md = bom_to_markdown(bom)

    # ── Diagram reference ─────────────────────────────────────────────────────
    if diagram_key:
        diagram_ref = (
            f"*Architecture diagram generated by Agent 3.*  \n"
            f"Object Storage key: `{diagram_key}`"
            + (f"  \nDownload: {diagram_url}" if diagram_url else "")
        )
    else:
        # Look for latest diagram in the bucket
        latest_key = f"{persistence_prefix}/{customer_id}/LATEST.json"
        if store.head(latest_key):
            diagram_ref = (
                f"*Architecture diagram available in OCI Object Storage.*  \n"
                f"Object Storage key: `{latest_key}`"
            )
            diagram_key = latest_key
        else:
            diagram_ref = (
                "*Architecture diagram: [TBD — run the Architecture Diagram agent "
                "(Agent 3) to generate a draw.io diagram for this POC.]*"
            )

    # ── Duration ──────────────────────────────────────────────────────────────
    duration = _infer_duration(notes_text)

    # ── Build prompt ──────────────────────────────────────────────────────────
    prompt = _PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        notes_text=notes_text[:5000],
        previous_jep_section=previous_jep_section,
        bom_md=bom_md,
        diagram_ref=diagram_ref,
        duration=duration,
    )

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info("Generating JEP: customer_id=%s customer_name=%r", customer_id, customer_name)
    content = text_runner(prompt, JEP_SYSTEM_MESSAGE)

    # ── Persist ───────────────────────────────────────────────────────────────
    result = save_doc(
        store, "jep", customer_id, content,
        {"customer_name": customer_name, "bom_source": bom.get("source", "stub")},
    )
    result["content"] = content
    result["bom"] = bom
    if diagram_key:
        result["diagram_key"] = diagram_key

    logger.info("JEP saved: version=%d key=%s", result["version"], result["key"])
    return result

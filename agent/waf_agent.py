"""
agent/waf_agent.py
------------------
OCI Well-Architected Framework (WAF) review agent (Agent 7 in fleet).

Each run:
  1. Reads context/{customer_id}/context.json (all prior agent outputs)
  2. Identifies notes not yet incorporated by this agent
  3. Reads previous WAF review (if any)
  4. Calls LLM with: full context summary + new notes + all agent references
  5. Produces a structured Markdown review across all 6 OCI WAF pillars
  6. Saves the review as a versioned document
  7. Updates context file with this run's results

OCI WAF Pillars
---------------
  1. Operational Excellence
  2. Security
  3. Reliability
  4. Performance Efficiency
  5. Cost Optimization
  6. Sustainability

Storage
-------
  Reads:  context/{customer_id}/context.json
          notes/{customer_id}/* (new notes only, diffed against context)
          waf/{customer_id}/LATEST.md (previous version, if any)
  Writes: waf/{customer_id}/v{n}.md
          waf/{customer_id}/LATEST.md
          waf/{customer_id}/MANIFEST.json
          context/{customer_id}/context.json (updated)
"""
from __future__ import annotations

import logging
from typing import Callable

from agent.context_store import (
    build_context_summary,
    get_new_notes,
    read_context,
    record_agent_run,
    write_context,
)
from agent.document_store import get_latest_doc, save_doc
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

AGENT_NAME = "waf"

WAF_PILLARS = [
    "Operational Excellence",
    "Security",
    "Reliability",
    "Performance Efficiency",
    "Cost Optimization",
    "Sustainability",
]

WAF_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud Infrastructure (OCI) Well-Architected Framework reviewer. "
    "You evaluate OCI architectures across all six WAF pillars: "
    "Operational Excellence, Security, Reliability, Performance Efficiency, "
    "Cost Optimization, and Sustainability. "
    "Rate each pillar as ✅ (strong), ⚠️ (needs attention), or ❌ (critical gap). "
    "Be specific: cite exact OCI services, configurations, and practices. "
    "Reference the customer's actual architecture and notes — do not give generic advice. "
    "Output ONLY the document content in Markdown format. No meta-commentary, no preamble."
)

_PROMPT_TEMPLATE = """\
Perform an OCI Well-Architected Framework review.

Customer: {customer_name}

{context_summary}

{new_notes_section}

{previous_waf_section}

{instructions}

Generate a complete WAF review in Markdown. Use this exact structure:

# {customer_name} — OCI Well-Architected Framework Review

## Executive Summary
[2–3 sentences summarising the overall architecture maturity and the top 3 priority actions.]

### Overall Rating
| Pillar | Rating | Summary |
|--------|--------|---------|
| Operational Excellence | ✅/⚠️/❌ | [one-line summary] |
| Security               | ✅/⚠️/❌ | [one-line summary] |
| Reliability            | ✅/⚠️/❌ | [one-line summary] |
| Performance Efficiency | ✅/⚠️/❌ | [one-line summary] |
| Cost Optimization      | ✅/⚠️/❌ | [one-line summary] |
| Sustainability         | ✅/⚠️/❌ | [one-line summary] |

---

## Pillar 1 — Operational Excellence

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: specific observation about monitoring, automation, CI/CD, runbooks, etc.]
- [Finding 2]
- [Finding 3 if applicable]

### Recommendations
- [Recommendation 1: specific OCI service or practice, e.g. "Enable OCI Logging Analytics for..."]
- [Recommendation 2]
- [Recommendation 3 if applicable]

---

## Pillar 2 — Security

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: identity, network security, encryption, secrets management, etc.]
- [Finding 2]
- [Finding 3 if applicable]

### Recommendations
- [Recommendation 1: e.g. "Use OCI Vault for all secrets rather than hardcoding in config"]
- [Recommendation 2]

---

## Pillar 3 — Reliability

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: HA topology, fault domains, backup/DR strategy, health checks, etc.]
- [Finding 2]
- [Finding 3 if applicable]

### Recommendations
- [Recommendation 1: e.g. "Deploy compute across 3 fault domains in the AD"]
- [Recommendation 2]

---

## Pillar 4 — Performance Efficiency

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: shape selection, GPU utilisation, network bandwidth, storage IOPS, etc.]
- [Finding 2]
- [Finding 3 if applicable]

### Recommendations
- [Recommendation 1: e.g. "Use RDMA cluster networking for GPU-to-GPU communication"]
- [Recommendation 2]

---

## Pillar 5 — Cost Optimization

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: reserved capacity vs on-demand, idle resources, storage tiers, etc.]
- [Finding 2]
- [Finding 3 if applicable]

### Recommendations
- [Recommendation 1: e.g. "Reserve GPU capacity with 1-year Universal Credits for 30–40% savings"]
- [Recommendation 2]

---

## Pillar 6 — Sustainability

**Rating**: ✅/⚠️/❌

### Findings
- [Finding 1: region energy efficiency, right-sizing, lifecycle policies, etc.]
- [Finding 2]

### Recommendations
- [Recommendation 1: e.g. "Enable OCI Object Storage lifecycle policies to tier cold data to Archive"]
- [Recommendation 2]

---

## Top Priority Actions

| Priority | Action | Pillar | Effort |
|----------|--------|--------|--------|
| 1 | [Most critical action] | [Pillar] | High/Medium/Low |
| 2 | [Second action] | [Pillar] | High/Medium/Low |
| 3 | [Third action] | [Pillar] | High/Medium/Low |

---
*Generated by OCI Agent Fleet — WAF Review Agent*
"""


def _extract_overall_rating(content: str) -> str:
    """Extract a simple overall rating from WAF content (most common rating symbol)."""
    import re
    # Search for each emoji as a literal sequence (⚠️ is multi-codepoint)
    counts = {
        "✅":  len(re.findall("✅",  content)),
        "⚠️": len(re.findall("⚠️", content)),
        "❌":  len(re.findall("❌",  content)),
    }
    if not any(counts.values()):
        return "unknown"
    return max(counts, key=lambda k: counts[k])


def generate_waf_review(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
) -> dict:
    """
    Generate or update an OCI WAF review document.

    Args:
        customer_id:   Customer identifier — bucket key prefix.
        customer_name: Human-readable customer name.
        store:         ObjectStoreBase instance.
        text_runner:   callable(prompt: str, system_message: str) -> str.

    Returns dict with keys:
        version (int), key (str), latest_key (str), content (str),
        overall_rating (str), context (dict)
    """
    # ── Read context + diff new notes ─────────────────────────────────────────
    context = read_context(store, customer_id, customer_name)
    if customer_name and not context.get("customer_name"):
        context["customer_name"] = customer_name

    new_note_keys, new_notes_text = get_new_notes(store, context, AGENT_NAME)
    context_summary = build_context_summary(context)

    # ── Previous WAF review ───────────────────────────────────────────────────
    previous_waf = get_latest_doc(store, "waf", customer_id)

    # ── Build prompt sections ─────────────────────────────────────────────────
    if context_summary:
        context_block = context_summary
    else:
        context_block = "(No prior agent outputs — review based on notes only.)"

    if new_notes_text:
        new_notes_section = (
            "New meeting notes to incorporate:\n"
            f"{new_notes_text[:4000]}"
        )
    elif not previous_waf:
        new_notes_section = "(No notes available — generate a skeleton WAF review.)"
    else:
        new_notes_section = "(No new notes — update WAF review based on latest agent outputs.)"

    if previous_waf:
        previous_waf_section = (
            "Previous WAF review (update and improve — do not repeat verbatim):\n"
            "```\n"
            + previous_waf[:2500]
            + "\n```"
        )
        instructions = (
            "Update the WAF review to reflect new notes and any changes in agent outputs. "
            "Re-evaluate ratings where new evidence warrants it."
        )
    else:
        previous_waf_section = ""
        instructions = "This is the first WAF review for this customer. Write a complete review."

    prompt = _PROMPT_TEMPLATE.format(
        customer_name=customer_name,
        context_summary=context_block,
        new_notes_section=new_notes_section,
        previous_waf_section=previous_waf_section,
        instructions=instructions,
    )

    # ── Generate ──────────────────────────────────────────────────────────────
    logger.info(
        "Generating WAF review: customer_id=%s new_notes=%d", customer_id, len(new_note_keys)
    )
    content = text_runner(prompt, WAF_SYSTEM_MESSAGE)

    # ── Persist doc ───────────────────────────────────────────────────────────
    overall_rating = _extract_overall_rating(content)
    result = save_doc(
        store, "waf", customer_id, content,
        {"customer_name": customer_name, "overall_rating": overall_rating},
    )

    # ── Update + write context ────────────────────────────────────────────────
    context = record_agent_run(
        context,
        AGENT_NAME,
        new_note_keys,
        {
            "version":        result["version"],
            "key":            result["key"],
            "overall_rating": overall_rating,
        },
    )
    write_context(store, customer_id, context)

    result["content"] = content
    result["overall_rating"] = overall_rating
    result["context"] = context
    logger.info(
        "WAF review saved: version=%d key=%s rating=%s",
        result["version"], result["key"], overall_rating,
    )
    return result

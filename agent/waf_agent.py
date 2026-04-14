"""
agent/waf_agent.py
-------------------
Well-Architected Framework (WAF) review agent.

Two modes:
  Standalone (diagram_context=None):
    Full narrative review across the five OCI WAF pillars, informed by all
    engagement context + notes.
  Orchestration (diagram_context provided):
    Topology gap analysis against the OCI WAF checklist derived from the
    official OCI WAF document. The LLM receives the actual node list from
    the diagram and emits machine-readable draw_instructions for the
    diagram_waf_orchestrator to apply.

Source for topology rules:
    https://docs.oracle.com/en/solutions/oci-best-practices/

Storage
-------
  Reads:  context/{customer_id}/context.json
  Writes: waf/{customer_id}/v{n}.md + LATEST.md + MANIFEST.json
          waf/{customer_id}/v{n}_prompt_log.json
          context/{customer_id}/context.json  (updated)
"""
from __future__ import annotations

import json
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
    get_best_base_doc,
    save_doc,
    save_prompt_log,
)
from agent.notifications import notify
from agent.persistence_objectstore import ObjectStoreBase

logger = logging.getLogger(__name__)

AGENT_NAME = "waf"

# ── System messages ────────────────────────────────────────────────────────────

WAF_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud Infrastructure Solutions Architect performing a "
    "Well-Architected Framework review based on the OCI WAF document "
    "(docs.oracle.com/en/solutions/oci-best-practices/). "
    "Assess the architecture across the five OCI WAF pillars: "
    "Security and Compliance, Reliability and Resilience, "
    "Performance and Cost Optimization, Operational Efficiency, "
    "and Distributed Cloud. "
    "Reference OCI-specific services and best practices. "
    "For each pillar write 2–4 sentences: current state assessment + top recommendation. "
    "End with a one-line summary rating: ✅ Well-Architected / ⚠️ Needs Improvement / ❌ Critical Gaps. "
    "Output ONLY the review in Markdown. No meta-commentary."
)

WAF_ORCHESTRATION_SYSTEM_MESSAGE = (
    "You are an Oracle Cloud Infrastructure Solutions Architect. "
    "You will receive a list of nodes present in an architecture diagram and a checklist "
    "of OCI Well-Architected Framework topology requirements sourced from the official OCI WAF document. "
    "Identify gaps, write a concise review (failing pillars only), and emit "
    "machine-readable draw_instructions so the diagram can be improved automatically. "
    "Output ONLY the WAF review in Markdown, followed by the JSON block. No meta-commentary."
)

# Source: https://docs.oracle.com/en/solutions/oci-best-practices/toc.htm
WAF_TOPOLOGY_CHECKLIST = """\
OCI Well-Architected Framework — Topology Requirements
Source: docs.oracle.com/en/solutions/oci-best-practices/

Check each rule against the NODES PRESENT list.

1. SECURITY AND COMPLIANCE
   - Public-facing apps (load_balancer or api_gateway present) → waf (OCI Web Application Firewall)
     must be present in ingress layer to guard against OWASP Top 10 threats.
   - Private resources accessible by admins → bastion or bastion_service must be present
     (OCI Managed Bastion replaces jump hosts; do NOT leave SSH open on public IPs).
   - NSG (network_security_group) or security_list must be present; NSGs preferred for
     micro-segmentation at VNIC level.
   - database / autonomous_database / mysql must NOT appear in ingress or compute layer;
     they must be in data or db layer (private subnet only).

2. RELIABILITY AND RESILIENCE
   - If two or more compute nodes → load_balancer required to eliminate SPOF.
   - If deployment_type == "single_ad" and node_count >= 4 → recommend "multi_ad" deployment.
   - If database present and no backup/dataguard/standby node → HA gap (note in narrative).

3. PERFORMANCE AND COST OPTIMIZATION
   - If node_count >= 6 and no autoscaling/instance_pool → suggest adding instance_pool.
   - No topology draw change needed for cost; note right-sizing opportunity in narrative.

4. OPERATIONAL EFFICIENCY
   - If no monitoring / logging / observability node → add OCI Monitoring (oci_type: monitoring)
     in a management or async layer.

5. DISTRIBUTED CLOUD
   - If deployment_type == "single_region" and DR/backup required in context →
     note multi-region gap (no draw change needed for single-region diagrams).

For each failing check produce ONE draw_instruction (max 3 total across all pillars).
Format: imperative verb + oci_type + layer/position.
Example: "Add a waf node (oci_type: waf) in the ingress layer before the load_balancer"
If all checks pass → return empty suggestions [].
"""

_STANDALONE_PROMPT_TEMPLATE = """\
Generate a Well-Architected Framework review for an OCI deployment.

Customer: {customer_name}

{context_summary}
{previous_waf_section}

Review each of the five OCI WAF pillars:
1. Security and Compliance
2. Reliability and Resilience
3. Performance and Cost Optimization
4. Operational Efficiency
5. Distributed Cloud

For each pillar:
- Assess the current architecture based on available context.
- State the top recommendation with specific OCI service names where applicable.

End with a one-line summary: ✅ Well-Architected / ⚠️ Needs Improvement / ❌ Critical Gaps

Output format (Markdown):

# {customer_name} — OCI Well-Architected Framework Review

## 1. Security and Compliance
[Assessment + recommendation]

## 2. Reliability and Resilience
[Assessment + recommendation]

## 3. Performance and Cost Optimization
[Assessment + recommendation]

## 4. Operational Efficiency
[Assessment + recommendation]

## 5. Distributed Cloud
[Assessment + recommendation]

---

**Overall:** [✅ / ⚠️ / ❌] [one-line summary]
"""

_ORCHESTRATION_PROMPT_TEMPLATE = """\
NODES PRESENT in diagram: {node_types}
Deployment type: {deployment_type}
Node count: {node_count}
Layers: {layers}

{checklist}

Now write a WAF review covering ONLY the failing pillars above (skip passing ones).
Use the node list to determine which checks fail.

Output format:

# WAF Review — Topology Gap Analysis

## Failing Pillars
[For each failing pillar: heading + 2-3 sentences explaining the gap and recommended fix.]

---

**Overall:** [✅ / ⚠️ / ❌] [one-line summary]

<!-- WAF_REFINEMENT_SUGGESTIONS
[
  {{"pillar":"<pillar>","draw_instruction":"<imperative instruction>","priority":"<high|medium|low>"}}
]
-->
"""


# ── Rating extraction ──────────────────────────────────────────────────────────

def _extract_overall_rating(content: str) -> str:
    """Extract ✅/⚠️/❌ from the Overall line. Returns ⚠️ if not found."""
    for line in content.splitlines():
        if "overall" in line.lower():
            for symbol in ("✅", "⚠️", "❌"):
                if symbol in line:
                    return symbol
    for symbol in ("✅", "⚠️", "❌"):
        if symbol in content:
            return symbol
    return "⚠️"


# ── Refinement suggestions parser ──────────────────────────────────────────────

_START_TAG = "<!-- WAF_REFINEMENT_SUGGESTIONS"
_END_TAG   = "-->"


def _parse_refinement_suggestions(content: str) -> list[dict]:
    """
    Extract the JSON list of draw_instructions embedded in the WAF content.
    Returns [] on any parse error or if the block is absent.
    """
    idx = content.find(_START_TAG)
    if idx == -1:
        return []
    end_idx = content.find(_END_TAG, idx + len(_START_TAG))
    if end_idx == -1:
        return []
    json_text = content[idx + len(_START_TAG):end_idx].strip()
    try:
        suggestions = json.loads(json_text)
        return [s for s in suggestions if isinstance(s, dict) and "draw_instruction" in s]
    except (json.JSONDecodeError, ValueError):
        logger.warning("WAF: failed to parse refinement suggestions JSON")
        return []


# ── Main entry point ───────────────────────────────────────────────────────────

def generate_waf(
    customer_id: str,
    customer_name: str,
    store: ObjectStoreBase,
    text_runner: Callable[[str, str], str],
    *,
    diagram_context: Optional[dict] = None,
    feedback: str = "",
) -> dict:
    """
    Generate or update a WAF review document.

    Args:
        customer_id:      Customer identifier — bucket key prefix.
        customer_name:    Human-readable customer name.
        store:            ObjectStoreBase instance.
        text_runner:      callable(prompt: str, system_message: str) -> str.
        diagram_context:  If provided, runs topology gap analysis (orchestration mode).
                          Expected keys: deployment_type, node_types, node_count, layers.
        feedback:         Optional SA free-text corrections (standalone mode only).

    Returns dict with keys:
        version (int), key (str), latest_key (str), content (str),
        overall_rating (str), refinement_suggestions (list), context (dict)
    """
    # ── Read context ───────────────────────────────────────────────────────────
    context = read_context(store, customer_id, customer_name)
    if customer_name and not context.get("customer_name"):
        context["customer_name"] = customer_name

    new_note_keys, _notes_text = get_new_notes(store, context, AGENT_NAME)
    context_summary = build_context_summary(context)

    # ── Build prompt ───────────────────────────────────────────────────────────
    if diagram_context is not None:
        # Orchestration mode: topology gap analysis
        node_types      = ", ".join(sorted(diagram_context.get("node_types", []))) or "(unknown)"
        deployment_type = diagram_context.get("deployment_type", "unknown")
        node_count      = diagram_context.get("node_count", 0)
        layers          = ", ".join(sorted(diagram_context.get("layers", []))) or "(unknown)"

        prompt = _ORCHESTRATION_PROMPT_TEMPLATE.format(
            node_types      = node_types,
            deployment_type = deployment_type,
            node_count      = node_count,
            layers          = layers,
            checklist       = WAF_TOPOLOGY_CHECKLIST,
        )
        system_msg = WAF_ORCHESTRATION_SYSTEM_MESSAGE
        mode_label = "orchestration"
    else:
        # Standalone mode: full narrative review
        context_block = f"{context_summary}\n" if context_summary else ""
        base_doc = get_best_base_doc(store, "waf", customer_id)

        if base_doc:
            previous_waf_section = (
                "Previous WAF review (use as reference; update and improve):\n"
                "```\n" + base_doc[:2000] + "\n```"
            )
        else:
            previous_waf_section = ""

        prompt = _STANDALONE_PROMPT_TEMPLATE.format(
            customer_name        = customer_name,
            context_summary      = context_block,
            previous_waf_section = previous_waf_section,
        )
        system_msg = WAF_SYSTEM_MESSAGE
        mode_label = "standalone"

    # ── Generate ───────────────────────────────────────────────────────────────
    logger.info("Generating WAF review: customer_id=%s mode=%s", customer_id, mode_label)
    content = text_runner(prompt, system_msg)

    # ── Parse rating and suggestions ───────────────────────────────────────────
    overall_rating         = _extract_overall_rating(content)
    refinement_suggestions = _parse_refinement_suggestions(content)

    # ── Persist doc ────────────────────────────────────────────────────────────
    result = save_doc(store, "waf", customer_id, content, {
        "customer_name":  customer_name,
        "overall_rating": overall_rating,
        "mode":           mode_label,
    })

    # ── Save prompt log ────────────────────────────────────────────────────────
    save_prompt_log(store, "waf", customer_id, result["version"], {
        "system_message":        system_msg,
        "prompt":                prompt,
        "response_length_chars": len(content),
        "new_note_keys":         new_note_keys,
        "mode":                  mode_label,
        "feedback_provided":     feedback.strip(),
        "diagram_context":       diagram_context,
    })

    # ── Update + write context ─────────────────────────────────────────────────
    context = record_agent_run(
        context,
        AGENT_NAME,
        new_note_keys,
        {
            "version":        result["version"],
            "key":            result["key"],
            "overall_rating": overall_rating,
            "mode":           mode_label,
        },
    )
    write_context(store, customer_id, context)

    result["content"]                = content
    result["overall_rating"]         = overall_rating
    result["refinement_suggestions"] = refinement_suggestions
    result["context"]                = context
    logger.info("WAF saved: version=%d key=%s rating=%s",
                result["version"], result["key"], overall_rating)

    notify("waf_generated", customer_id,
           f"WAF v{result['version']} generated for {customer_name} [{overall_rating}]")
    return result

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
# Kept for reference; orchestration mode now uses _annotate_checklist() instead.
WAF_TOPOLOGY_CHECKLIST = """\
OCI Well-Architected Framework — Topology Requirements
Source: docs.oracle.com/en/solutions/oci-best-practices/
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
The checklist below has been PRE-EVALUATED in Python against the actual diagram.
Each item is marked [✅ PASS], [❌ FAIL], or [⚠️ WARN].
Trust these evaluations — do NOT re-evaluate them yourself.

{annotated_checklist}

YOUR TASKS:
1. Write a brief narrative section for EACH item marked [❌ FAIL] or [⚠️ WARN].
   Skip [✅ PASS] items entirely.
2. For each [❌ FAIL] item that has a topology fix, emit ONE draw_instruction in the JSON block.
   Do NOT emit draw_instructions for [✅ PASS] or [⚠️ WARN] items (warnings are narrative-only).
3. If ALL items are [✅ PASS], write "No topology gaps found." and emit an empty suggestions list.

Output format:

# WAF Review — Topology Gap Analysis

## Failing / Warning Pillars
[For each FAIL/WARN item: heading + 2-3 sentences: what is missing and why it matters.]

---

**Overall:** [✅ / ⚠️ / ❌] [one-line summary]

<!-- WAF_REFINEMENT_SUGGESTIONS
[
  {{"pillar":"<pillar>","draw_instruction":"<imperative instruction with (oci_type: X) and layer>","priority":"<high|medium|low>"}}
]
-->
"""


# ── Python checklist evaluator ─────────────────────────────────────────────────

# Canonical type sets — lower-case; all aliases the LLM or orchestrator might use
_PUBLIC_FACING   = {"load_balancer", "load balancer", "api_gateway", "api gateway",
                    "flexible load balancer"}
_WAF_TYPES       = {"waf", "oci_waf", "web_application_firewall",
                    "web application firewall"}
_BASTION_TYPES   = {"bastion", "bastion_service", "bastion service",
                    "oci bastion", "oci_bastion"}
_NSG_TYPES       = {"network_security_group", "nsg", "security_list", "security list",
                    "network security group"}
_DB_TYPES        = {"database", "autonomous_database", "mysql", "autonomous database",
                    "oracle database", "db system", "db_system"}
_LB_TYPES        = {"load_balancer", "load balancer", "flexible load balancer"}
_MONITORING_TYPES= {"monitoring", "logging", "observability", "oci_monitoring",
                    "oci monitoring", "logging analytics", "logging_analytics"}
_POOL_TYPES      = {"autoscaling", "instance_pool", "auto scaling", "instance pool",
                    "auto_scaling"}


def _annotate_checklist(diagram_context: dict) -> str:
    """
    Evaluate every OCI WAF topology rule in Python and return a pre-annotated
    checklist string.  The LLM receives facts, not questions — it only writes
    narrative for items already marked FAIL.

    This eliminates the hallucination problem where the LLM suggests missing
    nodes that were already added by a prior orchestration cycle.
    """
    node_types   = {t.lower() for t in diagram_context.get("node_types", [])}
    node_count   = diagram_context.get("node_count", 0)
    depl_type    = diagram_context.get("deployment_type", "single_ad")

    has_public   = bool(node_types & _PUBLIC_FACING)
    has_waf      = bool(node_types & _WAF_TYPES)
    has_bastion  = bool(node_types & _BASTION_TYPES)
    has_nsg      = bool(node_types & _NSG_TYPES)
    has_lb       = bool(node_types & _LB_TYPES)
    has_monitor  = bool(node_types & _MONITORING_TYPES)
    has_pool     = bool(node_types & _POOL_TYPES)
    compute_nodes = [t for t in node_types if "compute" in t or t == "instance"]

    lines = [
        "OCI Well-Architected Framework — Pre-Evaluated Checklist",
        f"(Node types present: {', '.join(sorted(node_types)) or '(none)'})",
        f"(node_count={node_count}, deployment_type={depl_type})",
        "",
        "1. SECURITY AND COMPLIANCE",
    ]

    # WAF check
    if has_public:
        if has_waf:
            lines.append("   [✅ PASS] WAF: public-facing component present AND waf node is present")
        else:
            lines.append(
                "   [❌ FAIL] WAF: public-facing component present (load_balancer/api_gateway) "
                "but NO waf node found → draw_instruction required: "
                "Add a waf node (oci_type: waf) in the ingress layer before the load_balancer"
            )
    else:
        lines.append("   [✅ PASS] WAF: no public-facing component — WAF not required")

    # Bastion check
    if has_bastion:
        lines.append("   [✅ PASS] Bastion: OCI Managed Bastion is present")
    else:
        lines.append(
            "   [⚠️ WARN] Bastion: no bastion/bastion_service found — "
            "recommend OCI Managed Bastion for secure admin access (narrative only)"
        )

    # NSG check
    if has_nsg:
        lines.append("   [✅ PASS] NSG/Security List: present")
    else:
        lines.append(
            "   [⚠️ WARN] NSG/Security List: not found — "
            "recommend network_security_group for micro-segmentation (narrative only)"
        )

    lines.append("")
    lines.append("2. RELIABILITY AND RESILIENCE")

    # Load balancer check
    if len(compute_nodes) >= 2:
        if has_lb:
            lines.append("   [✅ PASS] Load Balancer: multiple compute nodes present AND load_balancer found")
        else:
            lines.append(
                f"   [❌ FAIL] Load Balancer: {len(compute_nodes)} compute nodes present "
                "but NO load_balancer — SPOF risk → draw_instruction required: "
                "Add a load_balancer node (oci_type: load_balancer) in the ingress layer"
            )
    else:
        lines.append(
            f"   [✅ PASS] Load Balancer: {len(compute_nodes)} compute node(s) — "
            "single instance, load balancer not required"
        )

    # Single-AD with many nodes
    if depl_type == "single_ad" and node_count >= 4:
        lines.append(
            f"   [⚠️ WARN] Deployment type: single_ad with {node_count} nodes — "
            "consider multi_ad for higher availability (narrative only)"
        )
    else:
        lines.append(f"   [✅ PASS] Deployment type: {depl_type} with {node_count} nodes — acceptable")

    lines.append("")
    lines.append("3. PERFORMANCE AND COST OPTIMIZATION")

    if node_count >= 6:
        if has_pool:
            lines.append("   [✅ PASS] Instance Pool/Autoscaling: present")
        else:
            lines.append(
                f"   [⚠️ WARN] Instance Pool: {node_count} nodes but no instance_pool/autoscaling "
                "— consider adding for elasticity (narrative only)"
            )
    else:
        lines.append(f"   [✅ PASS] Instance Pool: {node_count} nodes — pool not required")

    lines.append("")
    lines.append("4. OPERATIONAL EFFICIENCY")

    if has_monitor:
        lines.append("   [✅ PASS] Monitoring/Logging: present")
    else:
        lines.append(
            "   [❌ FAIL] Monitoring/Logging: no monitoring/logging/observability node found → "
            "draw_instruction required: "
            "Add a monitoring node (oci_type: monitoring) in the async layer"
        )

    lines.append("")
    lines.append("5. DISTRIBUTED CLOUD")
    lines.append(
        "   [✅ PASS] Single-region topology — multi-region DR is an engagement-level "
        "decision, not a diagram topology change (narrative note only if relevant)"
    )

    lines.append("")
    lines.append(
        "REMINDER: Emit draw_instructions ONLY for items explicitly marked "
        "[❌ FAIL] above. Do NOT suggest fixes for [✅ PASS] or [⚠️ WARN] items."
    )

    return "\n".join(lines)


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
        # Orchestration mode: topology gap analysis.
        # The checklist is pre-evaluated in Python so the LLM receives facts
        # (✅ PASS / ❌ FAIL) rather than having to evaluate them itself.
        # This eliminates the hallucination where the LLM suggests nodes that
        # were already added by a prior orchestration cycle.
        annotated = _annotate_checklist(diagram_context)

        prompt = _ORCHESTRATION_PROMPT_TEMPLATE.format(
            annotated_checklist = annotated,
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

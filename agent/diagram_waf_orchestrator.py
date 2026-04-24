"""
agent/diagram_waf_orchestrator.py
-----------------------------------
Orchestration loop: Drawing ↔ WAF quality gate.

After the drawing agent produces a diagram, this loop:
  1. Runs the WAF agent in orchestration mode (topology gap analysis).
  2. If WAF finds topology gaps, amends the compiled spec DIRECTLY —
     parses the oci_type and layer from each draw_instruction and adds
     the missing node to the correct subnet.  No LLM is called for
     the amendment: the WAF instructions already contain all necessary
     information in machine-readable form.
  3. Re-runs the layout engine + draw.io generator on the amended spec.
  4. Repeats up to max_iterations times.
  5. Stops when WAF is satisfied (empty suggestions) or the spec stops
     changing (stalemate), or max_iterations is reached.

The loop is called server-side — completely invisible to the SA.
The SA only sees clarification questions (drawing phase) and the final
diagram + WAF result when the loop is done.

Pure Python — no FastAPI imports. Independently testable.
"""
from __future__ import annotations

import copy
import functools
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agent.layout_engine import spec_to_draw_dict
from agent.drawio_generator import generate_drawio
from agent.waf_agent import generate_waf

logger = logging.getLogger(__name__)


# ── Synthetic item ─────────────────────────────────────────────────────────────

@dataclass
class _AddedNode:
    """Duck-type compatible with ServiceItem for layout engine calls."""
    id: str
    label: str
    oci_type: str
    layer: str
    notes: str = ""


# ── Layer synonym table ────────────────────────────────────────────────────────

_LAYER_NORMALIZE: dict[str, str] = {
    "management": "async",
    "security":   "ingress",
    "monitoring": "async",
    "logging":    "async",
    "observability": "async",
}

# Maps canonical layer name → preferred subnet group suffix in the spec
_LAYER_TO_SUBNET_SUFFIX: dict[str, str] = {
    "ingress": "pub_sub_box",
    "compute": "app_sub_box",
    "data":    "db_sub_box",
}


# ── Spec amendment ─────────────────────────────────────────────────────────────

def _collect_existing_types(spec: dict) -> set[str]:
    """Return the set of oci_type values already present in the spec."""
    types: set[str] = set()

    def _from_node_list(nodes: list) -> None:
        for n in nodes:
            t = n.get("type", n.get("oci_type", "")).lower()
            if t:
                types.add(t)

    for region in spec.get("regions", []):
        for ad in region.get("availability_domains", []):
            for sub in ad.get("subnets", []):
                _from_node_list(sub.get("nodes", []))
        for comp in region.get("compartments", []):
            for sub in comp.get("subnets", []):
                _from_node_list(sub.get("nodes", []))
        _from_node_list(region.get("oci_services", []))
        _from_node_list(region.get("shared_services", []))
        _from_node_list(region.get("gateways", []))

    return types


def _amend_spec_from_suggestions(
    spec: dict,
    suggestions: list[dict],
) -> tuple[dict, list[_AddedNode]]:
    """
    Deterministically add WAF-suggested nodes to the compiled spec.

    Parses each draw_instruction string for:
      - oci_type  → "(oci_type: <type>)" pattern
      - layer     → first occurrence of a valid layer name

    Nodes that are already present (by oci_type) are skipped to avoid
    duplicates.  The amended spec is a deep copy of the input.

    Returns (amended_spec, list_of_added_node_metadata).
    """
    spec = copy.deepcopy(spec)
    existing_types = _collect_existing_types(spec)
    added: list[_AddedNode] = []

    for sug in suggestions:
        instr = sug.get("draw_instruction", "")

        # ── Extract oci_type ───────────────────────────────────────────────────
        oci_m = re.search(r'\(oci_type:\s*([^)]+)\)', instr)
        if not oci_m:
            logger.debug("WAF amendment: no oci_type in instruction %r — skip", instr)
            continue
        oci_type = oci_m.group(1).strip().lower()

        if oci_type in existing_types:
            logger.debug("WAF amendment: %s already present — skip", oci_type)
            continue
        existing_types.add(oci_type)

        # ── Extract layer ──────────────────────────────────────────────────────
        layer_m = re.search(
            r'\b(external|ingress|compute|async|data|management|security'
            r'|monitoring|logging|observability)\b',
            instr, re.IGNORECASE,
        )
        raw_layer = layer_m.group(1).lower() if layer_m else "compute"
        layer = _LAYER_NORMALIZE.get(raw_layer, raw_layer)

        node_id    = f"{oci_type.replace(' ', '_')}_waf_added"
        node_label = oci_type.replace("_", " ").title()
        node_dict  = {"id": node_id, "type": oci_type, "label": node_label}
        added_node = _AddedNode(id=node_id, label=node_label, oci_type=oci_type, layer=layer)

        target_suffix = _LAYER_TO_SUBNET_SUFFIX.get(layer)   # None for async/external
        regions = spec.get("regions", [])
        placed  = False

        for region in regions:
            if not placed:
                for ad in region.get("availability_domains", []):
                    for sub in ad.get("subnets", []):
                        sub_id = sub.get("id", "")
                        if target_suffix and (
                            sub_id.endswith(target_suffix)
                            or target_suffix in sub_id
                        ):
                            sub.setdefault("nodes", []).append(node_dict)
                            placed = True
                            break
                    if placed:
                        break

            if not placed:
                for comp in region.get("compartments", []):
                    for sub in comp.get("subnets", []):
                        sub_id = sub.get("id", "")
                        if target_suffix and (
                            sub_id.endswith(target_suffix)
                            or target_suffix in sub_id
                        ):
                            sub.setdefault("nodes", []).append(node_dict)
                            placed = True
                            break
                    if placed:
                        break

            if placed:
                break

        if not placed:
            # async / external / unmatched → services column / shared services
            if regions:
                if "shared_services" in regions[0]:
                    regions[0]["shared_services"].append(node_dict)
                else:
                    regions[0].setdefault("oci_services", []).append(node_dict)

        added.append(added_node)
        logger.info(
            "WAF amendment: added oci_type=%s layer=%s node_id=%s",
            oci_type, layer, node_id,
        )

    return spec, added


# ── Diagram context helper ─────────────────────────────────────────────────────

def _build_diagram_context(draw_result: dict, items: list) -> dict:
    """Build WAF context with both expected services and rendered diagram facts."""
    node_to_resource_map = draw_result.get("node_to_resource_map") or {}
    spec = draw_result.get("spec") or {}
    draw_dict = draw_result.get("draw_dict") or {}
    draw_nodes = draw_dict.get("nodes", [])
    actual_node_ids = {str(node.get("id", "")) for node in draw_nodes if node.get("id")}

    actual_node_types = sorted({
        str(node.get("type", "unknown")).lower()
        for node in draw_nodes
        if isinstance(node, dict) and node.get("type")
    })
    actual_layers = sorted({
        v.get("layer", "")
        for node_id, v in node_to_resource_map.items()
        if node_id in actual_node_ids and isinstance(v, dict) and v.get("layer")
    })
    node_count = len(draw_dict.get("nodes", []))
    edge_count = len(draw_dict.get("edges", []))

    expected_items = [
        item for item in items
        if getattr(item, "layer", "") != "external"
        and getattr(item, "notes", "") not in {"best practice", "injected_baseline"}
    ]
    expected_node_types = sorted({
        str(item.oci_type).lower()
        for item in expected_items
        if getattr(item, "oci_type", "")
    })
    missing_expected_nodes = [
        {
            "id": item.id,
            "oci_type": str(item.oci_type).lower(),
            "label": item.label,
            "layer": item.layer,
        }
        for item in expected_items
        if item.id not in actual_node_ids
    ]

    return {
        "deployment_type": spec.get("deployment_type", "single_ad"),
        "node_types":      actual_node_types,
        "actual_node_types": actual_node_types,
        "expected_node_types": expected_node_types,
        "missing_expected_nodes": missing_expected_nodes,
        "node_count":      node_count,
        "edge_count":      edge_count,
        "layers":          actual_layers,
    }


# ── Main loop ──────────────────────────────────────────────────────────────────

async def run_diagram_waf_loop(
    *,
    items: list,
    base_prompt: str,
    deployment_hints: dict,
    draw_result: dict,
    customer_id: str,
    customer_name: str,
    diagram_name: str,
    client_id: str,
    object_store,
    text_runner: Callable,
    run_pipeline_fn: Callable,   # kept for signature compatibility; not called for amendments
    max_iterations: int = 3,
) -> dict:
    """
    Run the WAF ↔ Drawing quality-gate loop.

    Amendment strategy (NEW):
      WAF draw_instructions carry explicit oci_type and layer in machine-readable
      form ("Add a waf node (oci_type: waf) in the ingress layer").  The loop
      parses these and adds the nodes DIRECTLY to the compiled spec, then
      re-runs the layout engine and draw.io generator.  No LLM is called for
      the amendment — this guarantees the change always lands and avoids
      token-limit truncation that broke the previous JSON-editor approach.

    Args:
        items:            ServiceItem list from the initial BOM parse.
        base_prompt:      Original (un-enriched) BOM prompt.
        deployment_hints: Hints dict (preserves multi_region_mode across calls).
        draw_result:      The status=ok result from the initial run_pipeline call.
        customer_id:      Customer identifier for WAF document storage.
        customer_name:    Human-readable customer name.
        diagram_name:     Diagram name for pipeline calls.
        client_id:        Client session ID.
        object_store:     ObjectStoreBase instance.
        text_runner:      sync callable(prompt, system_message) -> str.
        run_pipeline_fn:  Kept for API compatibility; not used for amendments.
        max_iterations:   Maximum WAF ↔ refine cycles (default 3).

    Returns:
        {
          "draw_result":   final draw_result dict,
          "waf_result":    final waf_result dict,
          "iterations":    int,
          "loop_history":  list of {iteration, waf_rating, applied, draw_instructions},
        }
    """
    import anyio

    loop_history: list[dict]  = []
    waf_result:   dict        = {}
    iteration     = 1
    prev_spec_str = json.dumps(draw_result.get("spec", {}), sort_keys=True)

    for iteration in range(1, max_iterations + 1):

        # ── WAF review ─────────────────────────────────────────────────────────
        diagram_context = _build_diagram_context(draw_result, items)
        logger.info(
            "WAF loop iteration=%d customer_id=%s node_types=%s",
            iteration, customer_id, diagram_context.get("node_types"),
        )

        def _run_waf(_ctx=diagram_context):
            return generate_waf(
                customer_id,
                customer_name,
                object_store,
                text_runner,
                diagram_context=_ctx,
            )

        waf_result  = await anyio.to_thread.run_sync(_run_waf)
        suggestions = waf_result.get("refinement_suggestions", [])

        loop_history.append({
            "iteration":         iteration,
            "waf_rating":        waf_result.get("overall_rating", "⚠️"),
            "applied":           len(suggestions),
            "draw_instructions": [s.get("draw_instruction", "") for s in suggestions],
        })

        if not suggestions:
            logger.info("WAF loop: no suggestions at iteration=%d — done", iteration)
            break

        if iteration == max_iterations:
            logger.info("WAF loop: max_iterations=%d reached — stopping", max_iterations)
            break

        # ── Amend spec from WAF draw_instructions ──────────────────────────────
        # Parse oci_type and layer directly from the machine-readable instruction
        # strings — no LLM call needed.  Any oci_type not yet present gets added
        # to the correct subnet (or services column) in the compiled spec.
        amended_spec, added_nodes = _amend_spec_from_suggestions(
            draw_result.get("spec", {}), suggestions
        )

        cur_spec_str = json.dumps(amended_spec, sort_keys=True)
        if cur_spec_str == prev_spec_str:
            # Nothing was actually added (all suggested types already present)
            # — WAF may be hallucinating or the checklist is inconsistent.
            logger.info(
                "WAF loop: spec unchanged at iteration=%d — stalemate, stopping",
                iteration,
            )
            break

        # ── Re-run layout engine on the amended spec ───────────────────────────
        # Build items_by_id for layout, extending with the newly added nodes
        items_by_id: dict = {i.id: i for i in items}
        for n in added_nodes:
            if n.id not in items_by_id:
                items_by_id[n.id] = n   # _AddedNode is duck-type compatible

        new_draw_dict = await anyio.to_thread.run_sync(
            functools.partial(spec_to_draw_dict, amended_spec, items_by_id)
        )

        # ── Re-generate draw.io XML ────────────────────────────────────────────
        output_path = draw_result.get("output_path")
        if output_path:
            await anyio.to_thread.run_sync(
                functools.partial(generate_drawio, new_draw_dict, output_path)
            )
            new_xml = await anyio.to_thread.run_sync(
                functools.partial(Path(output_path).read_text)
            )
        else:
            new_xml = draw_result.get("drawio_xml", "")

        # ── Update node_to_resource_map so next WAF cycle sees the new nodes ───
        new_ntorm = dict(draw_result.get("node_to_resource_map") or {})
        for n in added_nodes:
            if n.id not in new_ntorm:
                new_ntorm[n.id] = {"oci_type": n.oci_type, "layer": n.layer, "label": n.label}

        # ── Advance draw_result ────────────────────────────────────────────────
        new_draw_result = dict(draw_result)
        new_draw_result["spec"]                = amended_spec
        new_draw_result["draw_dict"]           = new_draw_dict
        new_draw_result["drawio_xml"]          = new_xml
        new_draw_result["node_to_resource_map"] = new_ntorm
        if "_refine_context" in new_draw_result:
            new_draw_result["_refine_context"]["prev_spec"] = json.dumps(amended_spec)

        draw_result   = new_draw_result
        prev_spec_str = cur_spec_str

    return {
        "draw_result":  draw_result,
        "waf_result":   waf_result,
        "iterations":   iteration,
        "loop_history": loop_history,
    }
